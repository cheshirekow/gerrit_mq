from __future__ import print_function
import json
import logging
import os
import shutil
import subprocess
import sys
import time

import requests
from gerrit_mq import common
from gerrit_mq import orm

def poll_query(gerrit, sql, poll_id, offset, limit):
  """
  Hit gerrit REST and read off the current queue of merge requests. Update the
  local cache database entries for any changes that have been updated since
  our last poll. Write the resulting ordered queue to the queue file.
  """

  for _, queue_time, ci_json in gerrit.get_merge_requests(offset, limit):

    ai_json = ci_json['owner']
    owner_query = (sql.query(orm.AccountInfo)
                   .filter_by(rid=ai_json['_account_id']))

    if owner_query.count() == 0:
      ai_sql = orm.AccountInfo(rid=ai_json['_account_id'],
                               name=ai_json.get('name', ''),
                               email=ai_json.get('email', ''),
                               username=ai_json.get('username', ''))
      sql.add(ai_sql)
      sql.commit()
    else:
      # TODO(josh): only update if it has changed. Alternatively, don't
      # query detailed user info at all during queue query, and just
      # periodically query user details for every user we know about.
      ai_sql = owner_query.first()
      ai_sql.name = ai_json.get('name', '')
      ai_sql.email = ai_json.get('email', '')
      ai_sql.username = ai_json.get('username', '')
      sql.commit()

    query = (sql
             .query(orm.ChangeInfo)
             .filter_by(project=ci_json['project'],
                        branch=ci_json['branch'],
                        change_id=ci_json['change_id']))
    if query.count() == 0:
      msg_meta = gerrit.get_message_meta(ci_json['change_id'],
                                         ci_json['current_revision'])
      ci_sql = orm.ChangeInfo(change_id=ci_json['change_id'],
                              poll_id=poll_id,
                              queue_time=queue_time,
                              priority=msg_meta.get('Priority', 100),
                              project=ci_json['project'],
                              branch=ci_json['branch'],
                              current_revision=ci_json['current_revision'],
                              subject=ci_json['subject'],
                              owner=ci_json['owner']['_account_id'],
                              message_meta=json.dumps(msg_meta))
      sql.add(ci_sql)
      sql.commit()
    else:
      ci_sql = query.first()
      # The change has been updated since we last saw it in the queue,
      # we'd better re-parse the metadata and update our local cache.
      if ci_sql.current_revision != ci_json['current_revision']:
        ci_sql.poll_id = poll_id
        ci_sql.queue_time = queue_time
        ci_sql.branch = ci_json['branch']
        ci_sql.current_revision = ci_json['current_revision']
        ci_sql.subject = ci_json['subject']
        ci_sql.owner = ci_json['owner']['_account_id']
        msg_meta = gerrit.get_message_meta(ci_sql.change_id,
                                           ci_sql.current_revision)
        ci_sql.message_meta = json.dumps(msg_meta)
        sql.commit()
      else:
        msg_meta = json.loads(ci_sql.message_meta)

    if ci_json.get('_more_changes', False):
      return True

  return False


def poll_gerrit(gerrit, sql, poll_id, limit=25):
  """
  Hit gerrit REST and read off the current queue of merge requests. Update the
  local cache database entries for any changes that have been updated since
  our last poll.
  """

  idx = 0
  while poll_query(gerrit, sql, poll_id, idx * limit, limit):
    idx += 1
    logging.info('gerrit reported that there are more items queued, '
                 'will query again')
    continue

  # Delete anything in the cache which did not show up during this poll
  sql.query(orm.ChangeInfo).filter(orm.ChangeInfo.poll_id != poll_id).delete()


def get_queue(sql, project_filter, branch_filter, offset, limit):
  """
  Return json serializable list of ChangeInfo dictionaries for available
  merge requests matching the given project and branch filters (as regular
  expressions)

  TODO(josh): filter out any which are IN_PROGRESS?
  """
  query = sql.query(orm.ChangeInfo)

  if project_filter is not None:
    query = query.filter(orm.ChangeInfo.project.like(project_filter))
  if branch_filter is not None:
    query = query.filter(orm.ChangeInfo.branch.like(branch_filter))

  query = query.order_by(orm.ChangeInfo.queue_time.desc())
  count = query.count()

  if offset > 0:
    query = query.offset(offset)

  if limit > 0:
    query = query.limit(limit)

  result_list = []
  for ci_sql in query:
    ci_json = ci_sql.as_dict()
    # TODO(josh): JOIN?
    owner_query = sql.query(orm.AccountInfo).filter_by(rid=ci_json['owner'])
    for owner in owner_query:
      ci_json['owner'] = owner.as_dict()
      break
    result_list.append(ci_json)

  return dict(count=count, result=result_list)


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

  assert from_version == '0.1.0'
  assert to_version == '0.2.0'

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
    new_status = orm.MergeStatus(rid=old_status.id,
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
  # TODO(josh): reverse this dependency
  from gerrit_mq import daemon

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
      daemon.add_or_update_account_info(sql, ai_obj)
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

