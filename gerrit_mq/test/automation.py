import io
import logging
import os
import shutil
import subprocess
import tempfile

import git
import requests

from gerrit_mq import daemon
from gerrit_mq.test import gerrit_docker

COMMIT_MSG = """Commit number {:4d} from test sequence

Hello, this is my detailed commit message.

Feature-Branch: {}
"""

def get_change_id(commit_msg):
  for line in commit_msg.splitlines():
    if ':' in line:
      key, value = line.split(':', 1)
      if 'Change-Id' in key:
        return value.strip()
  return None


def get_or_clone_repo(config, user, repo_path, project, env):
  try:
    return git.Repo(repo_path)
  except (git.NoSuchPathError, git.InvalidGitRepositoryError):
    logging.exception('workspace does not appear to be a git repository: %s',
                      repo_path)

  parentdir = os.path.dirname(repo_path)
  try:
    os.makedirs(parentdir)
  except OSError:
    pass

  # clone the repository if needed
  repository_url = 'ssh://{}@{}:{}/{}.git'.format(
      user,
      config['gerrit.ssh.host'],
      config['gerrit.ssh.port'],
      project)
  logging.info('attemping to clone %s', repository_url)

  subprocess.check_call(['git', 'clone', repository_url, repo_path],
                        cwd=parentdir, env=env)

  # create a git repository object for this working repository
  return git.Repo(repo_path)

def create_reviews(config, gerrit, args):
  """
  Create some non-conflicting feature branches based off of master, submit each
  as a review, and optionally mark them approved and queued.
  """

  repo_path = args.repo_path
  if repo_path is None:
    repo_path = tempfile.mkdtemp()

  try:
    os.makedirs(os.path.dirname(repo_path))
  except OSError:
    pass

  # write out the identity file
  if args.identity is not None:
    identity_path = args.identity
    identity_is_temp = False
  else:
    fdn, identity_path = tempfile.mkstemp()
    os.close(fdn)
    with open(identity_path, 'w') as outfile:
      outfile.write(gerrit_docker.ADMIN_PRIVATE_KEY)
    identity_is_temp = True

  env = dict(os.environ)
  env['GIT_SSH_COMMAND'] = "ssh -i {}".format(identity_path)
  logging.info('GIT_SSH_COMMAND: %s', env['GIT_SSH_COMMAND'])

  repo = get_or_clone_repo(config, args.user, repo_path, 'mq_test', env)
  daemon.cleanup_repo(repo)

  # download the commit hook
  req = requests.get('http://localhost:8081/tools/hooks/commit-msg',
                     stream=True)
  local_path = os.path.join(repo_path, '.git/hooks/commit-msg')
  with io.open(local_path, 'wb') as outfile:
    for chunk in req.iter_content(chunk_size=1024):
      if chunk: # filter keep-alive
        outfile.write(chunk)
  os.chmod(local_path, 0o755)

  logging.info('Pulling %s', args.branch)
  subprocess.check_call(['git', 'checkout', args.branch], cwd=repo_path,
                        env=env)
  subprocess.check_call(['git', 'pull'], cwd=repo_path, env=env)

  review_dict = {'message': 'auto',
                 'labels': {},
                 'notify': 'NONE'}
  if args.approve:
    review_dict['labels']['Code-Review'] = 2
  if args.queue:
    review_dict['labels']['Merge-Queue'] = 1

  for feature_idx in range(args.num_features):
    feature_branch = 'feature_{:04d}'.format(feature_idx)
    logging.info('Creating feature branch %s', feature_branch)
    subprocess.check_call(['git', 'checkout', args.branch], cwd=repo_path)
    subprocess.check_call(['git', 'checkout', '-b', feature_branch],
                          cwd=repo_path, env=env)
    modified_filepath = os.path.join(repo_path,
                                     'file_{:04d}.txt'.format(feature_idx))
    with open(modified_filepath, 'a') as outfile:
      outfile.write('Hello!\n')

    subprocess.check_call(['git', 'add', '-A'], cwd=repo_path, env=env)
    message = COMMIT_MSG.format(feature_idx, feature_branch)
    subprocess.check_call(['git', 'commit', '-m', message],
                          cwd=repo_path, env=env)
    commit_msg = subprocess.check_output(['git', 'log', '-n', '1',
                                          feature_branch],
                                         cwd=repo_path, env=env)
    change_id = get_change_id(commit_msg)

    subprocess.check_call(['git', 'push', 'origin', 'HEAD:refs/for/master'],
                          cwd=repo_path, env=env)
    subprocess.check_call(['git', 'push', '-u', 'origin', feature_branch,
                           '--force'], cwd=repo_path, env=env)
    subprocess.check_call(['git', 'checkout', 'master'], cwd=repo_path, env=env)


    if args.approve or args.merge:
      gerrit.set_review(change_id, '1', review_dict)

  if not args.keep_clone:
    shutil.rmtree(repo_path)

  if identity_is_temp:
    os.remove(identity_path)

BUILD_PY = """#!/usr/bin/python

import os
import sys

this_dir = os.path.dirname(__file__)
fail_sentinel = os.path.join(this_dir, 'fail_sentinel.txt')
if os.path.exists(fail_sentinel):
  sys.exit(1)
else:
  sys.exit(0)

"""

PF_COMMIT_MSG = """Commit number {commit_no:4d} should {expected_result}

Hello, this is my detailed commit message.

Feature-Branch: {feature_branch}
"""

def create_pass_fail(config, gerrit, args):
  """
  Create some non-conflicting feature branches based off of master, submit each
  as a review, and optionally mark them approved and queued.
  """

  repo_path = args.repo_path
  if repo_path is None:
    repo_path = tempfile.mkdtemp()

  try:
    os.makedirs(os.path.dirname(repo_path))
  except OSError:
    pass

  repo = daemon.get_or_clone_repo(config, repo_path, 'mq_test')
  daemon.cleanup_repo(repo)
  subprocess.check_call(['git', 'fetch', '--prune'], cwd=repo_path)

  hook_path = os.path.join(repo_path, '.git/hooks/commit-msg')
  if not os.path.exists(hook_path):
    logging.info('Downloading %s', hook_path)
    hook_url = config['gerrit.rest.url'] + '/tools/hooks/commit-msg'
    subprocess.check_call(['curl', '--insecure', '-Lo', hook_path, hook_url])
    os.chmod(hook_path, 0o755)

  logging.info('Pulling %s', args.branch)
  subprocess.check_call(['git', 'checkout', args.branch], cwd=repo_path)
  subprocess.check_call(['git', 'pull'], cwd=repo_path)

  review_dict = {'message': 'auto',
                 'labels': {},
                 'notify': 'NONE'}
  if args.approve:
    review_dict['labels']['Code-Review'] = 2
  if args.queue:
    review_dict['labels']['Merge-Queue'] = 1

  fail_sentinel = os.path.join(repo_path, 'fail_sentinel.txt')
  build_py = os.path.join(repo_path, 'build.py')


  for feature_idx, pf_spec in enumerate(args.changes):
    feature_branch = 'feature_{:04d}'.format(feature_idx)
    logging.info('Creating feature branch %s', feature_branch)
    subprocess.check_call(['git', 'checkout', args.branch],
                          cwd=repo_path)
    subprocess.check_call(['git', 'checkout', '-b', feature_branch],
                          cwd=repo_path)
    modified_filepath = os.path.join(repo_path,
                                     'file_{:04d}.txt'.format(feature_idx))
    with open(modified_filepath, 'a') as outfile:
      outfile.write('Hello!\n')
    with open(build_py, 'w') as outfile:
      outfile.write(BUILD_PY)

    if pf_spec == 'F':
      expected_result = 'fail'
      with open(fail_sentinel, 'w') as outfile:
        pass
    else:
      expected_result = 'pass'

    subprocess.check_call(['git', 'add', '-A'], cwd=repo_path)
    message = PF_COMMIT_MSG.format(commit_no=feature_idx,
                                   expected_result=expected_result,
                                   feature_branch=feature_branch)

    subprocess.check_call(['git', 'commit', '-m', message], cwd=repo_path)
    commit_msg = subprocess.check_output(['git', 'log', '-n', '1',
                                          feature_branch],
                                         cwd=repo_path)
    change_id = get_change_id(commit_msg)
    subprocess.check_call(['git', 'push', 'origin',
                           'HEAD:refs/for/{}'.format(args.branch)],
                          cwd=repo_path)
    subprocess.check_call(['git', 'push', '-u', 'origin', feature_branch,
                           '--force'], cwd=repo_path)

    if args.approve or args.queue:
      gerrit.set_review(change_id, '1', review_dict)

  if not args.keep_clone:
    shutil.rmtree(repo_path)
