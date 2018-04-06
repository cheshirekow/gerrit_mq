"""Web frontend for inspecting the merge queue."""

import datetime
import cStringIO
import logging
import os

import flask

from gerrit_mq import orm
from gerrit_mq import functions

HTML_ESCAPE_TABLE = {
    "&": "&amp;",
    '"': "&quot;",
    "'": "&apos;",
    ">": "&gt;",
    "<": "&lt;",
}


def html_escape_file(infile, max_bytes):
  """
  Escape special characters so that the content is html-embeddable
  """
  buf = cStringIO.StringIO()
  bytes_read = 0
  for char in iter(lambda: infile.read(1).decode('utf-8', 'replace'), ''):
    # TODO(josh): fix this in a better way
    if ord(char) < 128:
      buf.write(HTML_ESCAPE_TABLE.get(char, char))
    else:
      # TODO(josh): is this OK for utf-8?
      buf.write(char)
    bytes_read += 1
    if bytes_read >= max_bytes:
      break

  # TODO(josh): Can we force this to be unicode? Create test case.
  return bytes_read, buf.getvalue()


def html_escape_tail(filename, max_bytes):
  """
  Escape special characters so that the content is html-embeddable
  """
  with open(filename, 'r') as infile:
    infile.seek(0, 2)  # move the file stream to the end of the file
    size_in_bytes = infile.tell()
    if max_bytes > size_in_bytes:
      infile.seek(0, 0)  # move back to the beginning
    else:
      infile.seek(-max_bytes, 2)
    _, content = html_escape_file(infile, max_bytes)
    return content


def extract_common_args(req_args):
  """
  Pull common query parameter arguments out of a query string
  """

  # TODO(josh): figure out FlaskSqlAlchemy and how to do this right.
  project_filter = req_args.get('project', None)
  branch_filter = req_args.get('branch', None)

  try:
    offset = max(int(req_args['offset']), 0)
  except (KeyError, ValueError):
    offset = 0

  try:
    limit = min(int(req_args['limit']), 500)
  except (KeyError, ValueError):
    limit = 25

  return project_filter, branch_filter, offset, limit


class Webfront(flask.Flask):

  def __init__(self, mq_config, gerrit, sql_factory):
    super(Webfront, self).__init__('gerrit_mq')
    self.debug = mq_config['webfront.flask_debug']
    self.gerrit = gerrit
    self.sql_factory = sql_factory
    self.mq_config = mq_config
    self.secret_key = mq_config['webfront.secret_key']

    self.add_url_rule('/gmq/cancel_merge', 'cancel_merge', self.cancel_merge)
    self.add_url_rule('/gmq/get_active_merge_status', 'get_active_merge_status',
                      self.get_active_merge_status)
    self.add_url_rule('/gmq/get_queue', 'get_queue', self.get_queue)
    self.add_url_rule('/gmq/get_history', 'get_history', self.get_history)
    self.add_url_rule('/gmq/get_merge_status', 'get_merge_status',
                      self.get_merge_status)
    self.add_url_rule('/gmq/get_daemon_status', 'get_daemon_status',
                      self.get_daemon_status)
    self.add_url_rule('/gmq/set_daemon_pause', 'set_daemon_pause',
                      self.set_daemon_pause)

    logging.info('Initialized webfront app')

  def get_queue(self):
    """
    Return json-encoded list of ChangeInfo objects for the current queue.

    Query params:
    `project` : SQL `LIKE` expression for projects to match
    `branch` : SQL `LIKE` expression for branches to match
    `offset` : start offset for pagination
    `limit` : maximum number of records to return
    """

    project_filter, branch_filter, offset, limit \
        = extract_common_args(flask.request.args)

    sql = self.sql_factory()
    count, result_list = functions.get_queue(sql, project_filter, branch_filter,
                                             offset, limit)
    sql.close()
    return flask.jsonify(count=count,
                         result=[ci.as_dict() for ci in result_list])

    # request_queue = [ci.as_dict() for ci in self.gerrit.get_merge_requests()]
    # return flask.jsonify(count=len(request_queue), result=request_queue)

  def get_history(self):
    """
    Return json-encoded list of MergeStatus objects.

    Query params:
      `project` : SQL `LIKE` expression for projects to match
      `branch` : SQL `LIKE` expression for branches to match
      `offset` : start offset for pagination
      `limit` : maximum number of records to return
    """
    project_filter, branch_filter, offset, limit \
        = extract_common_args(flask.request.args)

    sql = self.sql_factory()
    query = sql.query(orm.MergeStatus)

    if project_filter is not None:
      query = query.filter(orm.MergeStatus.project.like(project_filter))
    if branch_filter is not None:
      query = query.filter(orm.MergeStatus.branch.like(branch_filter))

    query = query.order_by(orm.MergeStatus.rid.desc())
    count = query.count()

    if offset > 0:
      query = query.offset(offset)

    if limit > 0:
      query = query.limit(limit)

    result = []
    for merge_sql in list(query):
      merge_json = merge_sql.as_dict()
      query = (sql.query(orm.MergeChange)
               .join(orm.MergeChange.owner)
               .filter(orm.MergeChange.merge_id == merge_sql.rid))
      merge_json['changes'] = []
      for change_sql in query:
        merge_json['changes'].append(change_sql.as_dict())
      result.append(merge_json)

    response = flask.jsonify(dict(count=count,
                                  result=result))
    sql.close()
    return response

  def get_merge_status(self):
    """
    Return json-encoded MergeStatus for a single merge

    Query params:
      `rid` : row id of the status to retrieve
    """
    sql = self.sql_factory()
    if 'rid' in flask.request.args:
      try:
        query_rid = int(flask.request.args['rid'])
      except (KeyError, ValueError):
        response = flask.jsonify({'status': 'ERROR',
                                  'reason': 'invalid rid'})
        response.status_code = 400
        sql.close()
        return response

      query = (sql
               .query(orm.MergeStatus)
               .filter(orm.MergeStatus.rid == query_rid))
    else:
      query = (sql
               .query(orm.MergeStatus).order_by(orm.MergeStatus.rid.desc())
               .limit(1))

    if query.count() < 1:
      sql.close()
      response = flask.jsonify({'status': 'ERROR',
                                'reason': "rid doesn't exist in db"})
      response.status_code = 404
      return response

    record_sql = query.first()
    record_json = record_sql.as_dict()
    record_json['changes'] = []

    query = (sql.query(orm.MergeChange)
             .filter(orm.MergeChange.merge_id == query_rid)
             .order_by(orm.MergeChange.request_time))
    for change_sql in query:
      record_json['changes'].append(change_sql.as_dict())

    sql.close()

    return flask.jsonify(record_json)

  def get_active_merge_status(self):
    """
    Return json-encoded MergeStatus for a single merge

    Query params:
      `rid` : row id of the status to retrieve
    """
    sql = self.sql_factory()
    query = (sql
             .query(orm.MergeStatus)
             # .filter(orm.MergeStatus.status
             #         == orm.StatusKey.IN_PROGRESS.value)
             .order_by(orm.MergeStatus.rid.desc())
             .limit(1))

    if query.count() < 1:
      sql.close()
      return flask.jsonify({})

    record_sql = query.first()
    record_json = record_sql.as_dict()
    record_json['changes'] = []

    query = (sql.query(orm.MergeChange)
             .filter(orm.MergeChange.merge_id == record_sql.rid)
             .order_by(orm.MergeChange.request_time))
    for change_sql in query:
      record_json['changes'].append(change_sql.as_dict())

    sql.close()
    return flask.jsonify(record_json)

  def cancel_merge(self):
    """
    Query params:
      `rid` : row id of the status to retrieve
    """
    sql = self.sql_factory()
    try:
      query_rid = int(flask.request.args['rid'])
    except (KeyError, ValueError):
      response = flask.jsonify({'status': 'ERROR',
                                'reason': 'invalid rid'})
      response.status_code = 400
      sql.close()
      return response

    query = (sql
             .query(orm.Cancellation)
             .filter(orm.Cancellation.rid == query_rid))
    if query.count() > 0:
      sql.close()
      return flask.jsonify({'status': 'SUCCESS',
                            'note': 'Already Canceled in DB'})

    row = orm.Cancellation(rid=query_rid, when=datetime.datetime.utcnow(),
                           who='Webfront')
    sql.add(row)
    sql.commit()
    sql.close()
    return flask.jsonify({'status': 'SUCCESS'})

  def get_daemon_status(self):
    """
    Return json-encoded info about the running daemon.
    """
    pausefile_path = self.mq_config['daemon.offline_sentinel_path']
    pidfile_path = self.mq_config['daemon.pidfile_path']
    daemon_pid = -1
    if os.path.exists(pidfile_path):
      try:
        with open(pidfile_path, 'r') as infile:
          daemon_pid = int(infile.read().strip())
      except (ValueError, IOError, OSError):
        pass

    status = {
        'alive': os.path.exists('/proc/{}/stat'.format(daemon_pid)),
        'paused': os.path.exists(pausefile_path),
        'pid': daemon_pid,
    }

    return flask.jsonify(status)

  def set_daemon_pause(self):
    """
    Touch or remove the pause sentinel
    """
    pausefile_path = self.mq_config['daemon.offline_sentinel_path']

    try:

      query_value = (flask.request.args.get('value', 'true') == 'true')
    except (KeyError, ValueError):
      query_value = True

    if query_value:
      with open(pausefile_path, 'w') as _:
        pass
    else:
      os.remove(pausefile_path)

    return self.get_daemon_status()
