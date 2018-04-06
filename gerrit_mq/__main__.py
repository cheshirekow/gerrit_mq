"""Entry point / launcher for gerrit-mq components."""

from __future__ import print_function
import argparse
import inspect
import io
import json
import logging
import os
import re
import sys
import traceback

import gerrit_mq
from gerrit_mq import common
from gerrit_mq import daemon
from gerrit_mq import functions
from gerrit_mq import orm

def class_to_cmd(name):
  intermediate = re.sub('(.)([A-Z][a-z]+)', r'\1-\2', name)
  return re.sub('([a-z0-9])([A-Z])', r'\1-\2', intermediate).lower()


class Command(object):
  """
  Base class making it a little easier to set up a complex argparse tree by
  specifying features of a command as memebers of a class.
  """

  default_log_level = 'info'

  @staticmethod
  def setup_parser(subparser):
    """
    Configure subparser for this command. Override in subclasses.
    """
    pass

  @classmethod
  def get_cmd(cls):
    """
    Return a string command name formulated by de-camael-casing the class
    name.
    """
    return class_to_cmd(cls.__name__)

  @classmethod
  def add_parser(cls, subparsers):
    """
    Add a subparser to the list of subparsers, and then call the classmethod
    to configure that subparser.
    """

    subparser = subparsers.add_parser(cls.get_cmd(), help=cls.__doc__)
    cls.setup_parser(subparser)

  @classmethod
  def run_args(cls, config, args):  # pylint: disable=unused-argument
    """
    Override this method to execute the command with the given parsed args.
    """
    raise RuntimeError('run_args unimplemented for object of type {}'
                       .format(getattr(cls, '__name__', '??')))


class PollGerrit(Command):
  """
  Hit gerrit REST and read off the current queue of merge requests. Write that
  to a json file.
  """
  @staticmethod
  def setup_parser(subparser):
    subparser.add_argument('--poll-id', type=int, default=0,
                           help="unique identifier for this poll. Used to "
                                "clear change queue of old changes")

  @classmethod
  def run_args(cls, config, args):
    gerrit = common.GerritRest(**config['gerrit.rest'])
    sql = orm.init_sql(config['db_url'])()

    if args.poll_id == 0:
      args.poll_id = functions.get_next_poll_id(sql)
    functions.poll_gerrit(gerrit, sql, args.poll_id)


class GetQueue(Command):
  """
  Retrieve the currently cached queue in json format
  """
  @staticmethod
  def setup_parser(subparser):
    subparser.add_argument('-p', '--project-filter', default=None,
                           help="which project to select queue items for")
    subparser.add_argument('-b', '--branch-filter', default=None,
                           help="regex to filter branches")
    subparser.add_argument('--offset', type=int, default=0,
                           help="offset for SQL query")
    subparser.add_argument('--limit', type=int, default=0,
                           help="maximum rows to return from SQL query")

  @classmethod
  def run_args(cls, config, args):
    session_factory = orm.init_sql(config['db_url'])
    _, queue = functions.get_queue(session_factory(), args.project_filter,
                                   args.branch_filter, args.offset, args.limit)
    queue = [item.as_dict() for item in queue]
    json.dump(queue, sys.stdout, indent=2, separators=(',', ': '))
    sys.stdout.write('\n')


class GetNext(Command):
  """
  Retrieve the next merge request.
  """
  @staticmethod
  def setup_parser(subparser):
    subparser.add_argument('-p', '--project-filter', default=None,
                           help="which project to select queue items for")
    subparser.add_argument('-b', '--branch-filter', default=None,
                           help="regex to filter branches")

  @classmethod
  def run_args(cls, config, args):
    session_factory = orm.init_sql(config['db_url'])
    queue = functions.get_queue(session_factory(), args.project_filter,
                                args.branch_filter, 0, -1)
    json.dump(queue, sys.stdout, indent=2, separators=(',', ': '))
    sys.stdout.write('\n')


class Webfront(Command):
  """
  Start the merge-queue master service.
  """

  default_log_level = 'warn'

  @classmethod
  def run_args(cls, config, args):
    gerrit = common.GerritRest(**config['gerrit.rest'])
    session_factory = orm.init_sql(config['db_url'])

    from gerrit_mq import webfront
    app = webfront.Webfront(config, gerrit, session_factory)
    app.run(**config['webfront.listen'])


class Daemon(Command):
  """
  Execute the daemon process.
  """

  @classmethod
  def run_args(cls, config, args):
    # we need to attempt to create directories before starting the logger
    # because one of the directories s the log directory
    # TODO(josh): implement this
    # common.create_directories(config)

    # We'll add a handler which puts log events in an actual file for review as
    # needed. We'll put the log file on a rotation where each log may grow up
    # to 1 megabyte with up to 10 backups
    filelog = logging.handlers.RotatingFileHandler(
        '{}/app.log'.format(config['log_path']),
        maxBytes=int(1e6), backupCount=10)

    # We'll add a timestamp to the format for this log
    format_str = ('%(asctime)s %(levelname)-8s %(filename)s [%(lineno)-3s] '
                  ': %(message)s')
    filelog.setFormatter(logging.Formatter(format_str))
    logging.getLogger('').addHandler(filelog)

    gerrit = common.GerritRest(**config['gerrit.rest'])
    session_factory = orm.init_sql(config['db_url'])
    app = daemon.MergeDaemon(config, gerrit, session_factory())

    watch_manifest = functions.get_watch_manifest()
    realpath_config = os.path.realpath(args.config_path)
    config_manifest = [(realpath_config, os.path.getmtime(realpath_config))]

    watch_manifest = sorted(watch_manifest + config_manifest)

    exit_code = 1
    try:
      app.run(watch_manifest)
      exit_code = 0
    except:  # pylint: disable=bare-except
      logging.exception('Exiting daemon due to uncought exception')

    sys.exit(exit_code)




class RenderTemplates(Command):
  """
  Render jinja2 templates into full html files.
  """

  @staticmethod
  def setup_parser(subparser):
    subparser.add_argument('outdir', nargs='?', default=None,
                           help="where to write the rendered files")

  @classmethod
  def run_args(cls, config, args):  # pylint: disable=unused-argument
    if args.outdir is None:
      args.outdir = config['webfront.pagedir_path']

    try:
      os.makedirs(args.outdir)
    except OSError:
      pass

    functions.render_templates(config, args.outdir)

class SyncAccountTable(Command):
  """
  Fetch account table from gerrit and store locally
  """

  @classmethod
  def run_args(cls, config, args):  # pylint: disable=unused-argument
    gerrit = common.GerritRest(**config['gerrit.rest'])
    session_factory = orm.init_sql(config['db_url'])
    functions.sync_account_db(gerrit, session_factory())


class MigrateDatabase(Command):
  """
  Migrate a database from one schema to another
  """

  @staticmethod
  def setup_parser(subparser):
    db_versions = ['0.1.0', '0.2.0', '0.2.1']
    subparser.add_argument('input_path',
                           help='Path to the source database')
    subparser.add_argument('output_path',
                           help='Path to the destination database')
    subparser.add_argument('-f', '--from-version', required=True,
                           choices=db_versions)
    subparser.add_argument('-t', '--to-version', required=True,
                           choices=db_versions)

  @classmethod
  def run_args(cls, config, args):
    gerrit = common.GerritRest(**config['gerrit.rest'])
    functions.migrate_db(gerrit, args.input_path, args.from_version,
                         args.output_path, args.to_version)


class FetchMissingAccountInfo(Command):
  """
  Download AccountInfo from gerrit
  """

  @staticmethod
  def setup_parser(subparser):
    subparser.add_argument('db_path', help='path to the database')

  @classmethod
  def run_args(cls, config, args):
    gerrit = common.GerritRest(**config['gerrit.rest'])
    functions.fetch_missing_account_info(gerrit, args.db_path)


class GzipOldLogs(Command):
  """
  Gzip files in an old log directory
  """
  @staticmethod
  def setup_parser(subparser):
    subparser.add_argument('source_dir',
                           help='Directory containing old unzipped logs')
    subparser.add_argument('dest_dir',
                           help='Directory containing new gzipped logs')

  @classmethod
  def run_args(cls, config, args):
    functions.gzip_old_logs(args.source_dir, args.dest_dir)


def iter_command_classes():
  """
  Return a list of all Command subclasses in this file.
  """

  for _, cmd_class in globals().iteritems():
    if (inspect.isclass(cmd_class)
            # pylint: disable=bad-continuation
            and issubclass(cmd_class, Command)
            and cmd_class is not Command):
      yield cmd_class


def main(argv):
  parser = argparse.ArgumentParser(prog="gerrit-mq", description=__doc__)
  parser.add_argument('-c', '--config-path',
                      default=os.path.expanduser('~/.gerrit-mq.py'),
                      help='path to config file')
  parser.add_argument('-l', '--log-level', default=None,
                      choices=['debug', 'info', 'warning', 'error'])
  parser.add_argument('-v', '--version', action='version',
                      version=gerrit_mq.VERSION)
  subparsers = parser.add_subparsers(dest='command', metavar='CMD')
  commands = [init() for init in iter_command_classes()]

  for command in commands:
    command.add_parser(subparsers)

  try:
    import argcomplete
    argcomplete.autocomplete(parser)
  except ImportError:
    pass

  args = parser.parse_args(argv)

  # set up main logger, which logs everything. We'll leave this one logging
  # to the console
  format_str = '%(levelname)-8s %(filename)s [%(lineno)-3s] : %(message)s'
  if args.log_level is None:
    log_level = 'info'
  else:
    log_level = args.log_level

  logging.basicConfig(level=getattr(logging, log_level.upper()),
                      format=format_str,
                      datefmt='%Y-%m-%d %H:%M:%S',
                      filemode='w')

  assert os.path.exists(args.config_path), (
      "The requested config file {} does not exist".format(args.config_path))

  try:
    globals_ = {}
    with io.open(args.config_path, 'r', encoding='utf-8') as infile:
      exec(infile.read(), globals_) # pylint: disable=W0122

  except:  # pylint: disable=bare-except
    traceback.print_exc()
    sys.stderr.write('Failed to execute config file\n')
    return 1

  config = common.ConfigDict(globals_)
  for command in commands:
    if args.command == command.get_cmd():
      if args.log_level is None:
        logging.getLogger('').setLevel(command.default_log_level.upper())
      return command.run_args(config, args)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
