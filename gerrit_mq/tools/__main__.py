import argparse
import inspect
import logging
import os
import re
import sys
import zipfile

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
  def run_args(cls, args):  # pylint: disable=unused-argument
    """
    Override this method to execute the command with the given parsed args.
    """
    raise RuntimeError('run_args unimplemented for object of type {}'
                       .format(getattr(cls, '__name__', '??')))


ZIP_MANIFEST = [
    'gerrit_mq/README.md',
    'gerrit_mq/__init__.py',
    'gerrit_mq/__main__.py',
    'gerrit_mq/common.py',
    'gerrit_mq/daemon.py',
    'gerrit_mq/functions.py',
    'gerrit_mq/master.py',
    'gerrit_mq/orm.py',
    'gerrit_mq/webfront.py',
    'gerrit_mq/templates/daemon.html.tpl',
    'gerrit_mq/templates/detail.html.tpl',
    'gerrit_mq/templates/history.html.tpl',
    'gerrit_mq/templates/index.html.tpl',
    'gerrit_mq/templates/layout.html.tpl',
    'gerrit_mq/templates/queue.html.tpl',
    'gerrit_mq/templates/script.js.tpl',
    'gerrit_mq/templates/style.css',
]

PYZIP_MAIN = """
import sys
import gerrit_mq.__main__

if __name__ == '__main__':
  sys.exit(gerrit_mq.__main__.main(sys.argv[1:]))
"""

class CreatePyzipExe(Command):
  """
  Create an executable python zipfile that can run the gerrit mq
  """

  @staticmethod
  def setup_parser(subparser):
    subparser.add_argument('outpath', help='where to write the file')


  @classmethod
  def run_args(cls, args):
    pardir = os.path.dirname(args.outpath)
    try:
      os.makedirs(pardir)
    except OSError:
      pass

    this_dir = os.path.dirname(__file__)
    base_dir = os.path.realpath(os.path.join(this_dir, os.path.pardir,
                                             os.path.pardir))
    with open(args.outpath, 'w') as outfile:
      outfile.write('#!/usr/bin/python\n')
      outfile.write('# PYTHON_ARGCOMPLETE_OK\n')
      with zipfile.ZipFile(outfile, 'w') as zfile:
        zfile.writestr('__main__.py', PYZIP_MAIN)
        for relpath in ZIP_MANIFEST:
          zfile.write(os.path.join(base_dir, relpath), arcname=relpath)
    os.chmod(args.outpath, 0o755)



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
  parser = argparse.ArgumentParser(prog="gerrit-mq/tools", description=__doc__)
  parser.add_argument('-l', '--log-level', default='info',
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
  for command in commands:
    if args.command == command.get_cmd():
      return command.run_args(args)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
