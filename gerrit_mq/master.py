"""Web frontend for inspecting the merge queue."""

from __future__ import print_function
import cStringIO

import flask
import sqlalchemy

from gerrit_mq import functions
from gerrit_mq import orm

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


class Master(flask.Flask):

  def __init__(self, mq_config, gerrit, sql_factory):
    super(Master, self).__init__('gerrit_mq')
    self.debug = mq_config['webfront.flask_debug']
    self.gerrit = gerrit
    self.sql_factory = sql_factory
    self.mq_config = mq_config
    self.secret_key = mq_config['webfront.secret_key']

    self.add_url_rule('/gmq/get_history', self.get_job)
    self.add_url_rule('/gmq/get_job', self.get_job)
    self.add_url_rule('/gmq/get_queue', self.get_queue)
    self.add_url_rule('/gmq/get_status', self.get_job)

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
    result = functions.get_queue(sql, project_filter, branch_filter, offset,
                                 limit)
    sql.close()
    return flask.jsonify(result)

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
    result = functions.get_history(sql, project_filter, branch_filter, offset,
                                   limit)
    sql.close()
    return flask.jsonify(result)

  def get_status(self):
    """
    Return json-encoded MergeStatus for a single merge

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

    query = sql.query(orm.MergeStatus).filter(orm.MergeStatus.rid == query_rid)
    for ms_sql in query:
      response = flask.jsonify(ms_sql.as_dict())
      sql.close()
      return response

    sql.close()
    response = flask.jsonify({'status': 'ERROR',
                              'reason': "rid doesn't exist in db"})
    response.status_code = 404
    return response

  def get_job(self):
    """
    Return the next available job for a given executor

    Query params:
      `builder_id` : the builder id
      `auth_key` : the builder secret
    """
    builder = None
    builder_id = flask.request.args.get('builder_id', None)

    for builder_config in self.mq_config['builders']:
      if builder_config['id'] == builder_id:
        builder = builder_config
        break

    if builder is None:
      response = flask.jsonify({'status': 'ERROR',
                                'reason':
                                'Builder not found {}'.format(builder_id)})
      response.status_code = 404
      return response

    if builder['auth_key'] != flask.request.args.get('auth_key', None):
      response = flask.jsonify({'status': 'ERROR',
                                'reason': 'Invalid credentials'})
      response.status_code = 403
      return response

    sql = self.sql_factory()
    query = (sql
             .query(sqlalchemy.func.min(orm.ChangeInfo.query_time))
             .group_by(orm.ChangeInfo.project, orm.ChangeInfo.branch)
             .order_by(orm.ChangeInfo.query_time.desc()))

    builder_queues = []
    for ci_sql in query:
      if (ci_sql.project, ci_sql.branch) in builder_queues:
        response = flask.jsonify(ci_sql.as_dict())
        sql.close()
        return response

    sql.close()
    return flask.jsonify({})


def main(mq_config, gerrit, session_factory):  # pylint: disable=unused-argument
  app = Master(mq_config, gerrit, session_factory)
  app.run(**mq_config['webfront.listen'])
