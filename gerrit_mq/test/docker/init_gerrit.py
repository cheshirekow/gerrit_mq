#!/usr/bin/python
"""Bootstrap gerrit configuration."""

from __future__ import print_function
import os
import shutil
import subprocess
import tempfile


TEST_PROJECT = 'mq_test'


class GerritCmd(object):
  """
  Thin wrapper around the gerrit command interface
  """

  def __init__(self, cmd_prefix):
    self.cmd_prefix = cmd_prefix

  def create_group(self, name, owner, description, group):
    subprocess.check_call(self.cmd_prefix + ['create-group', name,
                                             '--owner', owner,
                                             '--description', description,
                                             '--group', group])

  def create_account(self, username, groups, full_name, email,
                     ssh_key, http_password=None):

    cmd = self.cmd_prefix + ['create-account', username,
                             '--full-name', full_name,
                             '--email', email,
                             '--ssh-key', ssh_key]
    for group in groups:
      cmd += ['--group', group]

    if http_password is not None:
      cmd += ['--http-password', http_password]
    subprocess.check_call(cmd)

  def create_project(self, name, owner, description,
                     empty_commit=False):
    cmd = self.cmd_prefix + ['create-project',
                             '--name', name,
                             '--owner', owner,
                             '--description', description]
    if empty_commit:
      cmd += ['--empty-commit']
    subprocess.check_call(cmd)

  def ls_groups(self, verbose=False):
    cmd = self.cmd_prefix + ['ls-groups']
    if verbose:
      cmd += ['--verbose']
    return subprocess.check_output(cmd)


def init_gerrit():
  gerrit_home = os.getenv('GERRIT_HOME')
  assert gerrit_home, "GERRIT_HOME not in the environment"

  gerrit = GerritCmd(['ssh',
                      '-i', '/home/gerrit2/.ssh/id_rsa',
                      '-p', '29418',
                      'admin@localhost',
                      'gerrit'])

  keyscan_cmd = ['ssh-keyscan', '-t', 'rsa', '-p', '29418', 'localhost']
  key_string = subprocess.check_output(keyscan_cmd).strip()
  key_type, key_b64 = key_string.split()[1:]

  known_hosts_path = os.path.join(gerrit_home, '.ssh/known_hosts')
  with open(known_hosts_path, 'w') as known_hosts:
    known_hosts.write('localhost {} {}\n'.format(key_type, key_b64))

  # Create test_group group and merge queue user
  gerrit.create_group('TestGroup', owner='Administrators',
                      description='"TestGroup Users"',
                      group='Administrators')

  # Get the UUID of the created test_group group
  group_lines = gerrit.ls_groups(verbose=True)
  group_lines = group_lines.splitlines()
  print(group_lines)

  test_group_uuid = None
  for group_line in group_lines:
    # Parts should be:
    # group_name, group_uuid, description, owner_name, owner_uuid, visible
    parts = group_line.split()
    if len(parts) > 2 and parts[0] == 'TestGroup':
      test_group_uuid = parts[1]

  assert test_group_uuid, "Failed to find test_group UUID"
  print('test_group UUID: {}'.format(test_group_uuid))

  # Setup the All-Projects configuration
  tmp_dir = tempfile.mkdtemp()
  os.chdir(tmp_dir)

  subprocess.check_call(['git', 'config', '--global', 'user.name', 'gerrit'])
  subprocess.check_call(['git', 'config', '--global', 'user.email',
                         'gerrit@example.com'])
  subprocess.check_call(['git', 'init'])
  subprocess.check_call(['git', 'remote', 'add', 'origin',
                         os.path.join(gerrit_home,
                                      'gerrit/git/All-Projects.git')])
  subprocess.check_call(['git', 'fetch', 'origin',
                         'refs/meta/config:refs/remotes/origin/meta/config'])
  subprocess.check_call(['git', 'checkout', 'meta/config'])

  with open(os.path.join(tmp_dir, 'groups'), 'a') as groupsfile:
    groupsfile.write('{}\tTestGroup\n'.format(test_group_uuid))

  shutil.copy(os.path.join(gerrit_home, 'all_projects.config'),
              os.path.join(tmp_dir, 'project.config'))
  subprocess.check_call(['git', 'commit', '-a', '-m', 'Bootstrap Config'])
  subprocess.check_call(['git', 'push', 'origin', 'meta/config:meta/config'])

  os.chdir(gerrit_home)
  shutil.rmtree(tmp_dir)

  # This key was generated during the construction of the docker image. It
  # can be used to authenticate any of the users we create
  pub_key_path = os.path.join(gerrit_home, '.ssh/id_rsa.pub')
  with open(pub_key_path, 'r') as pubkey_file:
    pub_key = pubkey_file.readline().strip()

  # Create users for tests
  http_password = 'IW/XXJ4G98nbrcSscjKJvIjHRgpoV7Ax4ptTtbQu2g'
  account_kwargs = dict(ssh_key='"{}"'.format(pub_key),
                        http_password=http_password)

  gerrit.create_project('mq_test',
                        owner='TestGroup',
                        description='"Test Project"',
                        empty_commit=True)

  gerrit.create_account('merge_queue',
                        full_name='"Merge Queue"',
                        email='mergequeue@test.com',
                        groups=['"Non-Interactive Users"',
                                'TestGroup'],
                        **account_kwargs)

  gerrit.create_account('test1',
                        full_name='"Test User 1"',
                        email='test1@test.com',
                        groups=['TestGroup'],
                        **account_kwargs)

  gerrit.create_account('test2',
                        full_name='"Test User 2"',
                        email='test2@test.com',
                        groups=['TestGroup'],
                        **account_kwargs)


if __name__ == '__main__':
  init_gerrit()
