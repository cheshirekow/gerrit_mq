from __future__ import print_function
import argparse
import inspect
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import traceback

from gerrit_mq import common
from gerrit_mq.test import automation
from gerrit_mq.test import gerrit_docker

# TODO(josh): dedup this infrastructure
def class_to_cmd(name):
  intermediate = re.sub('(.)([A-Z][a-z]+)', r'\1-\2', name)
  return re.sub('([a-z0-9])([A-Z])', r'\1-\2', intermediate).lower()


class Command(object):
  """
  Base class making it a little easier to set up a complex argparse tree by
  specifying features of a command as memebers of a class.
  """

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

class GerritDocker(Command):
  @staticmethod
  def setup_parser(parser):
    subparsers = parser.add_subparsers(
        help='build the test image, or control the test container',
        dest='command')

    build_parser = subparsers.add_parser('build', help='build an image')
    build_parser.add_argument(
        '-b', '--build-dir', default=None,
        help='use this directory as the docker build dir. Implies --no-rm')
    build_parser.add_argument(
        '-n', '--no-rm', action='store_true',
        help="Don't clean resources copied to docker directory after building")
    build_parser.add_argument(
        '-u', '--uid', type=int, default=os.getuid(),
        help='uid of the user inside the docker container.')
    build_parser.add_argument(
        '-g', '--gerrit-version', default='2.11.3',
        help='gerrit version string to download/install')

    start_parser = subparsers.add_parser('start', help='start a container')
    start_parser.add_argument(
        '-d', '--debug', action='store_true',
        help='If true, will run the container in the foreground and will remove'
             ' when ended')
    start_parser.add_argument(
        '-D', '--dry-run', action='store_true',
        help='Dry run, prints the command that it would run and then exits')

    subparsers.add_parser('stop', help='stop the container')
    subparsers.add_parser('rm', help='remove the container')

  @classmethod
  def run_args(cls, config, args):  # pylint: disable=unused-argument
    if args.command == 'build':
      gerrit_docker.build_image(args.build_dir, args.gerrit_version, args.uid,
                                args.no_rm)
    elif args.command == 'start':
      gerrit_docker.start_container(args.debug, args.dry_run)
    elif args.command == 'stop':
      gerrit_docker.stop_container()
    elif args.command == 'rm':
      gerrit_docker.remove_container()


class StartNginx(Command):
  """
  Start nginx with a proxy-pass to the webfront and configured to serve
  pages from the webroot and logs from the logdirectory.
  """

  @classmethod
  def run_args(cls, config, args):
    this_dir = os.path.realpath(os.path.dirname(__file__))
    tpl_args = dict(
        pagedir_path=os.path.realpath(os.path.join(this_dir, '..', 'pages')),
        logdir_path=config['log_path'],
        webfront_port=config['webfront.listen.port']
    )

    import jinja2
    with open(os.path.join(this_dir, 'nginx_fg.conf'), 'r') as tplfile:
      template = jinja2.Template(tplfile.read())

    with tempfile.NamedTemporaryFile(delete=False) as tmpfile:
      tmpfile.write(template.render(**tpl_args))
      tmpfile_path = tmpfile.name

    logging.info('Starting nginx with config %s', tmpfile_path)
    cmd = ['nginx', '-p', this_dir, '-c', tmpfile_path, '-g',
           'error_log /tmp/nginx-error.log;']
    nginx_proc = subprocess.Popen(cmd)

    try:
      while nginx_proc.poll() is None:
        time.sleep(0.5)
    except KeyboardInterrupt:
      pass

    while nginx_proc.poll() is None:
      try:
        nginx_proc.send_signal(signal.SIGTERM)
        time.sleep(0.1)
      except KeyboardInterrupt:
        pass

    if nginx_proc.returncode == 0:
      logging.info('Nginx exited cleanly')
    else:
      logging.error('Failed to start nginx. %d', nginx_proc.returncode)
      logging.error('Command was:\n  %s', ' '.join(cmd))
      with open(tmpfile_path, 'r') as infile:
        lines = ['{:03d} {}'.format(idx, content)
                 for idx, content in enumerate(infile)]
      logging.error('Config was:\n' + ''.join(lines))

    os.remove(tmpfile_path)
    sys.exit(nginx_proc.returncode)


class CreateReviews(Command):
  """
  Create some non-conflicting feature branches based off of master, submit each
  as a review, and optionally mark them approved and queued.
  """

  @staticmethod
  def setup_parser(subparser):
    subparser.add_argument('num_features', type=int,
                           help='Number of feature branches to create/submit')
    subparser.add_argument('--approve', action='store_true',
                           help='Mark the changes as approved')
    subparser.add_argument('--queue', action='store_true',
                           help='Mark the changes for merge')
    subparser.add_argument('--keep-clone', action='store_true',
                           help="don't delete the clone from the local FS")
    subparser.add_argument('--repo-path', default=None,
                           help='clone the test repo to this location')

  @classmethod
  def run_args(cls, config, args):
    if args.queue:
      args.approve = True

    config['gerrit.rest.username'] = 'test1'
    gerrit = common.GerritRest(**config['gerrit.rest'])
    automation.create_reviews(config, gerrit, args)



def iter_command_classes():
  """
  Return a list of all Command subclasses in this file.
  """

  for _, cmd_class in globals().iteritems():
    if (inspect.isclass(cmd_class)
        and issubclass(cmd_class, Command)
        and cmd_class is not Command):
      yield cmd_class


def main(argv):
  parser = argparse.ArgumentParser(prog="gerrit-mq/test", description=__doc__)
  parser.add_argument('-c', '--config-path',
                      default=os.path.expanduser('~/.gerrit-mq.py'),
                      help='path to config file')
  parser.add_argument('-l', '--log-level', default='warning',
                      choices=['debug', 'info', 'warning', 'error'])
  subparsers = parser.add_subparsers(dest='command')
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
  logging.basicConfig(level=getattr(logging, args.log_level.upper()),
                      format=format_str,
                      datefmt='%Y-%m-%d %H:%M:%S',
                      filemode='w')

  assert os.path.exists(args.config_path), (
      "The requested config file {} does not exist".format(args.config_path))

  try:
    globals_ = globals()
    execfile(args.config_path, globals_)
  except:  # pylint: disable=bare-except
    traceback.print_exc()
    sys.stderr.write('Failed to execute config file\n')
    return 1

  assert 'CONFIG' in globals_, (
      "Configuration file must define CONFIG dictionary")

  config = common.ConfigDict(globals_['CONFIG'])
  for command in commands:
    if args.command == command.get_cmd():
      return command.run_args(config, args)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
