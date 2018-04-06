from __future__ import print_function
import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import zipfile

import jinja2
import requests
from gerrit_mq import common
from gerrit_mq import orm


def add_or_update_account_info(sql, ai_obj):
  """
  Update the AccountInfo object from gerrit json, or create it if it's new.
  """
  query = (sql
           .query(orm.AccountInfo)
           .filter(orm.AccountInfo.rid == ai_obj.account_id)
           .limit(1))
  if query.count() > 0:
    ai_sql = query.first()
    for field in ['name', 'email', 'username']:
      setattr(ai_sql, field, getattr(ai_obj, field))
  else:
    kwargs = ai_obj.as_dict()
    kwargs['rid'] = kwargs.pop('_account_id')
    for key in ['name', 'email', 'username']:
      if key not in kwargs:
        kwargs[key] = '<none>'
    ai_sql = orm.AccountInfo(**kwargs)
    sql.add(ai_sql)


def get_next_poll_id(sql):
  """
  Return the next unused poll id
  """

  from sqlalchemy.sql.expression import func
  query = sql.query(func.max(orm.ChangeInfo.poll_id))
  last_poll_id = query.scalar()
  if last_poll_id is None:
    return 1
  else:
    return last_poll_id + 1


def poll_gerrit(gerrit, sql, poll_id):
  """
  Hit gerrit REST and read off the current queue of merge requests. Update the
  local cache database entries for any changes that have been updated since
  our last poll. Write the resulting ordered queue to the queue file.
  """

  request_queue = gerrit.get_merge_requests()
  for changeinfo in request_queue:
    # Take this opportunity to to update the AccountInfo table
    # with the owner info
    add_or_update_account_info(sql, changeinfo.owner)

    priority = changeinfo.message_meta.get('Priority', 100)
    ci_sql = orm.ChangeInfo(project=changeinfo.project,
                            branch=changeinfo.branch,
                            change_id=changeinfo.change_id,
                            subject=changeinfo.subject,
                            current_revision=changeinfo.current_revision,
                            owner_id=changeinfo.owner.account_id,
                            message_meta=json.dumps(changeinfo.message_meta),
                            queue_time=changeinfo.queue_time,
                            poll_id=poll_id,
                            priority=priority)
    sql.add(ci_sql)
  sql.commit()

  # Delete anything in the cache which did not show up during this poll
  (sql.query(orm.ChangeInfo)
   .filter(orm.ChangeInfo.poll_id != poll_id)
   .delete())
  sql.commit()


def get_queue(sql, project_filter=None, branch_filter=None,
              offset=None, limit=None):
  """
  Return list of ChangeInfo objects matching the given project and branch
  filters (as SQL LIKE expressions)

  Returns a tuple of (`count`, `result_list`) where `count` is the size of
  the query without `offset` or `limit`.

  TODO(josh): filter out any which are IN_PROGRESS?
  """
  query = sql.query(orm.ChangeInfo)

  if project_filter is not None:
    query = query.filter(orm.ChangeInfo.project.like(project_filter))
  if branch_filter is not None:
    query = query.filter(orm.ChangeInfo.branch.like(branch_filter))

  query = query.order_by(orm.ChangeInfo.poll_id.desc(),
                         orm.ChangeInfo.priority.asc(),
                         orm.ChangeInfo.queue_time.asc())
  count = query.count()

  if offset is not None and offset > 0:
    query = query.offset(offset)

  if limit is not None and limit > 0:
    query = query.limit(limit)

  return count, [common.ChangeInfo(**ci_sql.as_dict()) for ci_sql in query]


def get_history(sql, project_filter, branch_filter, offset, limit):
  """
  Return json serializable list of MergeStatus dictionaries for available
  merge requests matching the given project and branch filters (as regular
  expressions)

  TODO(josh): filter out any which are IN_PROGRESS?
  """
  query = sql.query(orm.MergeStatus)

  if project_filter is not None:
    query = query.filter(orm.MergeStatus.project.like(project_filter))
  if branch_filter is not None:
    query = query.filter(orm.MergeStatus.branch.like(branch_filter))

  query = query.order_by(orm.MergeStatus.end_time.desc())
  count = query.count()

  if offset > 0:
    query = query.offset(offset)

  if limit > 0:
    query = query.limit(limit)

  return dict(count=count, result=[ms_sql.as_dict() for ms_sql in query])


def sync_account_db(gerrit, sql):
  """
  Synchronize local account table to gerrit account table
  """

  page_size = 25
  for page_idx in range(10000):
    offset = page_idx * page_size
    json_list = gerrit.get('accounts/?start={offset}&n={page_size}&o=DETAILS'
                           .format(offset=offset, page_size=page_size))
    for ai_json in json_list:
      query = (sql
               .query(orm.AccountInfo)
               .filter(orm.AccountInfo.rid == ai_json['_account_id']))
      if query.count() > 0:
        for ai_sql in query:
          ai_sql.name = ai_json.get('name', '<none>')
          ai_sql.email = ai_json.get('email', '<none>')
          ai_sql.username = ai_json.get('username', '<none>')
          sql.commit()
          break
      else:
        kwargs = dict(ai_json)
        kwargs['rid'] = kwargs.pop('_account_id')
        for key in ['name', 'email', 'username']:
          if key not in kwargs:
            kwargs[key] = '<none>'

        ai_sql = orm.AccountInfo(**kwargs)
        sql.add(ai_sql)
        sql.commit()

    if len(json_list) < 1 or '_more_accounts' not in json_list[-1]:
      break


def migrate_db(gerrit, input_path, from_version, output_path, to_version):
  """
  Migrate a database from one schema version to another
  """

  if from_version == '0.1.0' and to_version == '0.2.0':
    migrate_db_v0p1p0_to_v0p2p0(gerrit, input_path, output_path)
  elif from_version == '0.2.0' and to_version == '0.2.1':
    migrate_db_v0p2p0_to_v0p2p1(input_path, output_path)


def migrate_db_v0p2p0_to_v0p2p1(input_path, output_path):
  """
  Split merge_history into merge_history and merge_changes.
  """

  tmp_path = input_path + '.mq_migration'

  if os.path.exists(tmp_path):
    logging.info('Removing stale temporary %s', tmp_path)
    os.remove(tmp_path)

  logging.info('Copying %s to %s', input_path, tmp_path)
  shutil.copyfile(input_path, tmp_path)

  logging.info('Creating merge_changes table')
  import sqlite3
  conn = sqlite3.connect(tmp_path)
  cur = conn.cursor()
  cur.execute('SELECT name FROM SQLITE_MASTER WHERE type="index"'
              ' AND tbl_name="merge_history"')
  indices = [row[0] for row in cur]
  for index in indices:
    logging.info('Dropping index %s from merge_history', index)
    cur.execute('DROP INDEX {}'.format(index))
  cur.execute('ALTER TABLE merge_history RENAME TO merge_history_v0p2p0')
  conn.commit()
  conn.close()

  try:
    os.remove(output_path)
  except OSError:
    pass

  os.rename(tmp_path, output_path)

  logging.info('Migrating rows')
  sql = orm.init_sql('sqlite:///{}'.format(output_path))()

  prev_migration_query = (sql.query(orm.MergeStatus)
                          .order_by(orm.MergeStatus.rid.desc())
                          .limit(1))
  source_query = sql.query(orm.MergeStatusV0p2p0)

  if prev_migration_query.count() > 0:
    prev_migration_last = -1
    for prev_status in prev_migration_query:
      prev_migration_last = prev_status.rid
      break

    logging.info('Detected previous migration, will migrate increment'
                 ' starting at row id %d', prev_migration_last)
    source_query = source_query.filter(orm.MergeStatusV0p1p0.id >
                                       prev_migration_last)

  last_print_time = 0
  query_count = source_query.count()
  for idx, old_status in enumerate(source_query):
    kwargs = {key: getattr(old_status, key) for key in
              ['rid', 'project', 'branch', 'start_time', 'end_time', 'status']}
    sql.add(orm.MergeStatus(**kwargs))

    kwargs = {key: getattr(old_status, key) for key in
              ['owner_id', 'change_id', 'request_time', 'msg_meta']}
    kwargs['merge_id'] = old_status.rid
    sql.add(orm.MergeChange(**kwargs))

    sql.commit()

    if time.time() - last_print_time > 0.5:
      last_print_time = time.time()
      progress = 100.0 * (idx + 1) / query_count
      sys.stdout.write('{:6d}/{:6d} [{:6.2f}%]\r'
                       .format(idx, query_count, progress))
      sys.stdout.flush()

  sys.stdout.write('{:6d}/{:6d} [{:6.2f}%]\n'
                   .format(query_count, query_count, 100.0))

  sql.close()

  conn = sqlite3.connect(output_path)
  cur = conn.cursor()
  cur.execute('DROP TABLE merge_history_v0p2p0')
  conn.commit()
  conn.close()


def migrate_db_v0p1p0_to_v0p2p0(gerrit, input_path, output_path):
  """
  Migrate a database from one schema version to another
  """
  tmp_path = input_path + '.mq_migration'

  if os.path.exists(tmp_path):
    logging.info('Removing stale temporary %s', tmp_path)
    os.remove(tmp_path)

  logging.info('Copying %s to %s', input_path, tmp_path)
  shutil.copyfile(input_path, tmp_path)

  logging.info('Renaming old table')
  import sqlite3
  conn = sqlite3.connect(tmp_path)
  cur = conn.cursor()
  cur.execute('ALTER TABLE merge_history RENAME TO merge_history_v0p1p0')
  conn.commit()
  conn.close()

  logging.info('Migrating rows')
  source_sql = orm.init_sql('sqlite:///{}'.format(tmp_path))()
  dest_sql = orm.init_sql('sqlite:///{}'.format(output_path))()

  prev_migration_query = (dest_sql.query(orm.MergeStatus)
                          .order_by(orm.MergeStatus.rid.desc())
                          .limit(1))

  source_query = source_sql.query(orm.MergeStatusV0p1p0)

  if prev_migration_query.count() > 0:
    prev_migration_last = -1
    for prev_status in prev_migration_query:
      prev_migration_last = prev_status.rid
      break

    logging.info('Detected previous migration, will migrate increment'
                 ' starting at row id %d', prev_migration_last)
    source_query = source_query.filter(orm.MergeStatusV0p1p0.id >
                                       prev_migration_last)

  last_print_time = 0
  query_count = source_query.count()
  for idx, old_status in enumerate(source_query):
    if old_status.result == orm.StatusKey.IN_PROGRESS.value:
      status = orm.StatusKey.CANCELED.value
    else:
      status = old_status.result

    changeinfo = None
    msg_meta = {}
    max_tries = 10
    sleep_duration = 2

    for try_idx in range(max_tries):
      try:
        if not changeinfo:
          changeinfo = gerrit.get_change(old_status.change_id)
        if not msg_meta:
          msg_meta = gerrit.get_message_meta(old_status.change_id,
                                             changeinfo.current_revision)
        break
      except requests.RequestException:
        logging.warn('Failed to poll gerrit for change %s %d/%d',
                     old_status.change_id, try_idx, max_tries)
        time.sleep(sleep_duration)

    if changeinfo is not None:
      owner_id = changeinfo.owner.account_id
    else:
      owner_id = -1
    new_status = orm.MergeStatusV0p2p0(rid=old_status.id,
                                       project='aircam',
                                       branch=old_status.target_branch,
                                       owner_id=owner_id,
                                       change_id=old_status.change_id,
                                       request_time=old_status.request_time,
                                       start_time=old_status.start_time,
                                       end_time=old_status.end_time,
                                       msg_meta=json.dumps(msg_meta),
                                       status=status)
    dest_sql.add(new_status)
    dest_sql.commit()

    if time.time() - last_print_time > 0.5:
      last_print_time = time.time()
      progress = 100.0 * (idx + 1) / query_count
      sys.stdout.write('{:6d}/{:6d} [{:6.2f}%]\r'
                       .format(idx, query_count, progress))
      sys.stdout.flush()

  sys.stdout.write('{:6d}/{:6d} [{:6.2f}%]\n'
                   .format(query_count, query_count, 100.0))
  os.remove(tmp_path)


MISSING_IDS_QUERY = """
SELECT DISTINCT(owner_id)
  FROM merge_history LEFT JOIN account_info
    ON owner_id = account_info.rid
  WHERE account_info.rid is NULL
  ORDER BY owner_id ASC
"""


def fetch_missing_account_info(gerrit, db_path):
  """
  Fetch any account info from gerrit that is missing in the database
  """

  import sqlite3

  logging.info('Querying missing ids')
  sql = orm.init_sql('sqlite:///{}'.format(db_path))()
  conn = sqlite3.connect(db_path)
  cur = conn.cursor()
  cur.execute(MISSING_IDS_QUERY)

  missing_ids = [row[0] for row in cur]
  num_missing = len(missing_ids)
  conn.close()

  logging.info('Fetching %d account infos from gerrit', num_missing)
  last_print_time = 0
  for idx, owner_id in enumerate(missing_ids):
    if time.time() - last_print_time > 1:
      last_print_time = time.time()
      progress = 100.0 * (idx + 1) / num_missing
      sys.stdout.write('{:6d}/{:6d} [{:6.2f}%]\r'
                       .format(idx, num_missing, progress))
      sys.stdout.flush()

    try:
      ai_json = gerrit.get('accounts/{}'.format(owner_id))
      ai_obj = common.AccountInfo(**ai_json)
      add_or_update_account_info(sql, ai_obj)
    except requests.RequestException:
      logging.warn('Failed to get account info for owner_id=%d', owner_id)
      continue
    except ValueError:
      logging.warn('Malformed json for owner_id=%d', owner_id)
      continue

  sys.stdout.write('{:6d}/{:6d} [{:6.2f}%]\n'
                   .format(num_missing, num_missing, 100.0))
  sql.close()


def gzip_old_logs(srcdir, destdir):
  """
  For any logfiles that are not already gzipped, gzip them and then create a
  zero sized stub.
  """

  last_print_time = 0
  max_num_logs = 1000000
  gzip_jobs = 0

  for idx in range(max_num_logs):
    if time.time() - last_print_time > 1.0:
      last_print_time = time.time()
      progress = 100.0 * (idx + 1) / max_num_logs
      sys.stdout.write('{:6d}/{:6d} [{:6.2f}%] ({:6d})\r'
                       .format(idx, max_num_logs, progress, gzip_jobs))
      sys.stdout.flush()

    for extension in ['log', 'stderr', 'stdout']:
      filename = '{:06d}.{}'.format(idx, extension)
      logpath = os.path.join(srcdir, filename)
      stub_path = os.path.join(destdir, filename)
      gzip_path = os.path.join(destdir, filename + '.gz')
      if os.path.exists(logpath) and not os.path.exists(gzip_path):
        gzip_jobs += 1
        with open(gzip_path, 'w') as outfile:
          subprocess.check_call(['gzip', '--stdout', logpath], stdout=outfile)
        with open(stub_path, 'w') as _:
          pass

  sys.stdout.write('{:6d}/{:6d} [{:6.2f}%] ({:6d})\n'
                   .format(max_num_logs, max_num_logs, 100.0, gzip_jobs))


def path_prefix_in(query_path, prefix_list):
  query_parts = query_path.split('/')

  for prefix in prefix_list:
    prefix_parts = prefix.split('/')
    if prefix_parts[:len(query_parts)] == query_parts:
      return True
  return False


# this struct will be passed as a ponter,
# so we don't have to worry about the right layout
class dl_phdr_info(ctypes.Structure):  # pylint: disable=invalid-name
  _fields_ = [
      ('padding0', ctypes.c_void_p),  # ignore it
      ('dlpi_name', ctypes.c_char_p),  # ignore the reset
  ]


class LibListStore(object):

  def __init__(self):
    self.liblist = []

  def handle_libinfo(self, info, size, data):  # pylint: disable=unused-argument
    self.liblist.append(info.contents.dlpi_name)
    return 0


def get_loaded_libraries():
  """
  Return a list of file paths for libraries which are loaded into the current
  interpreter process
  """

  # NOTE(josh): c_void_p changed to c_char_p
  callback_t = ctypes.CFUNCTYPE(ctypes.c_int,
                                ctypes.POINTER(dl_phdr_info),
                                ctypes.POINTER(ctypes.c_size_t),
                                ctypes.c_char_p)

  dl_iterate_phdr = ctypes.CDLL('libc.so.6').dl_iterate_phdr

  # NOTE(josh): c_void_p replaced with c_char_p
  dl_iterate_phdr.argtypes = [callback_t, ctypes.c_char_p]
  dl_iterate_phdr.restype = ctypes.c_int

  list_store = LibListStore()
  dl_iterate_phdr(callback_t(list_store.handle_libinfo), "dummy")
  return list_store.liblist


def get_watch_manifest(ignore_prefixes=None):
  """
  Return a list of file paths and mtimes for files that should trigger a restart
  if they change.
  """

  if ignore_prefixes is None:
    ignore_prefixes = []

  zipfiles_on_path = []
  for component in sys.path:
    if component and zipfile.is_zipfile(component):
      realpath_to_zip = os.path.realpath(component)
      zipfiles_on_path.append((realpath_to_zip,
                               os.path.getmtime(realpath_to_zip)))

  zipfiles = [zfile for (zfile, _) in zipfiles_on_path]
  module_files = []
  for _, module in sys.modules.items():
    if hasattr(module, '__file__'):
      realpath_to_module = os.path.realpath(module.__file__)

      if path_prefix_in(realpath_to_module, zipfiles):
        logging.info('Skipping zipfile module %s', realpath_to_module)
        continue
      elif path_prefix_in(realpath_to_module, ignore_prefixes):
        logging.info('Skipping ignored module %s', realpath_to_module)
        continue
      else:
        module_files.append((realpath_to_module,
                             os.path.getmtime(realpath_to_module)))

  so_files = []
  for so_path in get_loaded_libraries():
    realpath_to_so = os.path.realpath(so_path)
    if path_prefix_in(realpath_to_module, ignore_prefixes):
      logging.info('Skipping ignored so %s', realpath_to_so)
      continue
    else:
      so_files.append((realpath_to_so, os.path.getmtime(realpath_to_so)))

  return sorted(zipfiles_on_path + module_files + so_files)


def get_changelist(manifest):
  """
  Given a list of (fullpath, mtime) return a list of paths whose current mtime
  is newer than the manifest mtime.
  """

  changelist = []
  for fullpath, mtime in manifest:
    if os.path.getmtime(fullpath) - mtime > 0.1:
      changelist.append(fullpath)

  return changelist


def get_real_argv():
  """
  Return the actual command line
  """

  with open('/proc/self/cmdline', 'r') as infile:
    return infile.read().split('\0')[:-1]


def restart_if_modified(watch_manifest, pidfile_path):
  """
  Restart the process if any file in the manifest has changed
  """

  changelist = get_changelist(watch_manifest)
  if changelist:
    logging.info('Detected a sourcefile change: \n  '
                 + '\n  '.join(changelist))
    argv = get_real_argv()
    os.remove(pidfile_path)
    os.execvp(sys.executable, argv)

class ZipFileLoader(jinja2.BaseLoader):
  """
  Implements a template loader which reads templates from a zipfile
  """

  def __init__(self, zipfile_path, base_directory):
    self.zipf = zipfile.ZipFile(zipfile_path)
    self.basedir = base_directory

  def __del__(self):
    self.zipf.close()

  def get_source(self, environment, template):
    try:
      fullpath = '{}/{}'.format(self.basedir, template)
      with self.zipf.open(fullpath) as fileobj:
        source = fileobj.read()
    except IOError:
      raise jinja2.TemplateNotFound(template,
                                    message='Fullpath: {}'.format(fullpath))

    return (source, None, lambda: False)


def render_templates(config, outdir):
  """
  Render jinja2 templates into the specified documentroot
  """

  pardir = os.path.dirname(__file__)
  pardir = os.path.dirname(pardir)
  logging.info('pardir: %s', pardir)

  if zipfile.is_zipfile(pardir):
    logging.info('reading data from zipfile')
    loader = ZipFileLoader(pardir, 'gerrit_mq/templates')
  else:
    logging.info('reading data from package directory')
    loader = jinja2.PackageLoader('gerrit_mq', 'templates')

  env = jinja2.Environment(loader=loader)

  for page in ['daemon', 'detail', 'history', 'index', 'queue']:
    template_name = '{}.html.tpl'.format(page)
    template = env.get_template(template_name)
    outpath = os.path.join(outdir, '{}.html'.format(page))
    with open(outpath, 'w') as outfile:
      outfile.write(template.render())  # pylint: disable=no-member
      outfile.write('\n')

  script_path = 'gerrit_mq/templates/script.js.tpl'
  style_path = 'gerrit_mq/templates/style.css'

  if zipfile.is_zipfile(pardir):
    with zipfile.ZipFile(pardir) as zfile:
      with zfile.open(script_path) as infile:
        js_lines = infile.readlines()
      with zfile.open(style_path) as infile:
        style_content = infile.read()
  else:
    with open(os.path.join(pardir, script_path)) as infile:
      js_lines = infile.readlines()
    with open(os.path.join(pardir, style_path)) as infile:
      style_content = infile.read()

  outpath = os.path.join(outdir, 'script.js')
  with open(outpath, 'w') as outfile:
    for line in js_lines:
      if line.startswith('var kGerritURL'):
        outfile.write('var kGerritURL = "{}";\n'
                      .format(config['gerrit.rest.url']))
      else:
        outfile.write(line)

  outpath = os.path.join(outdir, 'style.css')
  with open(outpath, 'w') as outfile:
    outfile.write(style_content)
