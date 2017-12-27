import logging
import os
import subprocess
import tempfile

from gerrit_mq import daemon

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

  repo = daemon.get_or_clone_repo(config, repo_path, 'mq_test')
  daemon.cleanup_repo(repo)

  logging.info('Pulling master')
  subprocess.check_call(['git', 'pull'], cwd=repo_path)

  review_dict = {'message': 'auto',
                 'labels': {},
                 'notify': 'NONE'}
  if args.approve:
    review_dict['labels']['Code-Review'] = 2
  if args.queue:
    review_dict['labels']['Merge-Queue'] = 1

  for feature_idx in range(args.num_features):
    feature_branch = 'feature_{:04d}'.format(feature_idx)
    logging.info('Creating feature branc %s', feature_branch)
    subprocess.check_call(['git', 'checkout', '-b', feature_branch],
                          cwd=repo_path)
    modified_filepath = os.path.join(repo_path,
                                     'file_{:04d}.txt'.format(feature_idx))
    with open(modified_filepath, 'a') as outfile:
      outfile.write('Hello!\n')

    subprocess.check_call(['git', 'add', '-A'], cwd=repo_path)
    message = COMMIT_MSG.format(feature_idx, feature_branch)
    subprocess.check_call(['git', 'commit', '-m', message], cwd=repo_path)
    commit_msg = subprocess.check_output(['git', 'log', '-n', '1',
                                          feature_branch],
                                         cwd=repo_path)
    change_id = get_change_id(commit_msg)

    subprocess.check_call(['git', 'push', 'origin', 'HEAD:refs/for/master'],
                          cwd=repo_path)
    subprocess.check_call(['git', 'push', '-u', 'origin', feature_branch,
                           '--force'],
                          cwd=repo_path)
    subprocess.check_call(['git', 'checkout', 'master'], cwd=repo_path)


    if args.approve or args.merge:
      gerrit.set_review(change_id, '1', review_dict)





  # 1. get_or_clone_repo
  # 2. pull master
  # 3. delete feature branches if they exist
  # 4. for each feature branch:
  #   a. checkout master
  #   b. create feature branch
  #   c. write to file
  #   d. add to index
  #   e. commit with message
  #   f. push to refs/for/master
  #   g. push to origin
