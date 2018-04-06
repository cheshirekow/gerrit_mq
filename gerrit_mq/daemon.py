"""
Merge-Queue daemon, repeatedly polls gerrit for any merge requests, and merges
them in serial order.
"""

import datetime
import json
import logging.handlers
import os
import re
import signal
import subprocess
import time

import git
import httplib2
import requests

from gerrit_mq import orm
from gerrit_mq import functions

# TODO(josh): split this module
# pylint: disable=too-many-lines

IN_SUBMISSION_TPL = """
Gerrit Merge-Queue has started to merge this change as part of merge #{1}.
{0}/detail.html?merge_id={1}
"""

RESULT_TPL = """
Merge #{1} {2}.
{0}/detail.html?merge_id={1}
"""

FAILURE_TPL = """

********************************
Merge failed on step {stepno}. The following command exited with nonzero status:
{command}

The return code was {retcode}
********************************

"""

STEP_TPL = """

-------------------------
Executing step: {stepno}
{command}
-------------------------

"""

GERRIT_CANCEL = """

********************************
Merge canceled because the following changes were evicted on gerrit by score
removal:
%s
********************************

"""

WEBFRONT_CANCEL = """

********************************
Merge was canceled through the webfront
  by: %s
  on: %s
********************************

"""


class QueueSpec(object):
  """
  Specification of a single queue of serialized merges
  """

  def __init__(self, project, branch, build_env, build_steps, name=None,
               merge_build_env=False, submit_with_rest=True, coalesce_count=0,
               submit_cmd=None):
    self.project = project
    self.branch = re.compile(branch)
    self.build_env = dict(build_env)
    self.build_steps = [list(step) for step in build_steps]
    self.coalesce_count = coalesce_count

    # Changes that were part of a failed coaleced verification. As long as
    # one of these changes is part of the current queue head, changes will
    # be made in serial order.
    self.dirty_changes = set()

    if name is None:
      assert re.escape(branch) == branch
      self.name = branch
    else:
      self.name = name

    self.merge_build_env = merge_build_env
    self.submit_with_rest = submit_with_rest
    if submit_cmd is None:
      self.submit_cmd = []
    else:
      self.submit_cmd = submit_cmd

    for key in self.build_env:
      value = self.build_env[key]

      # Environment variables like PATH or PYTHONPATH may be specified as
      # lists or tuples, in which case we join the elements with a
      # pathseparator to make the path string.
      if key.endswith('PATH') and (isinstance(value, list)
                                   or isinstance(value, tuple)):
        self.build_env[key] = ':'.join(value)

  def get_workspace(self, base_path):
    """
    Return the repo directory for this queue
    """

    return os.path.join(base_path, self.project, self.name)

  def get_environment(self, config):
    """
    Return the environment used to execute commands for this queue
    """

    if config.get('daemon.merge_build_env', False):
      subenv = os.environ.copy()
      subenv.update(self.build_env)
    else:
      subenv = dict(self.build_env)

    if 'daemon.ccache.path' in config:
      subenv['CCACHE_DIR'] = config['daemon.ccache.path']
    return subenv


def mark_gerrit_change_as_in_submission(gerrit, changeinfo,
                                        webfront_url, merge_rid):
  """
  Posts a review to a gerrit change informing owner and reviewers that it is
  being merged.
  """
  message = IN_SUBMISSION_TPL.format(webfront_url, merge_rid)
  review_dict = {'message': message,
                 'labels': {'Merge-Queue': 0},
                 'notify': 'NONE'}  # don't email on merge started
  gerrit.set_review(changeinfo.change_id, changeinfo.current_revision,
                    review_dict)


def get_result_message(webfront_url, merge_rid, merge_result):
  """
  Get the message to post to gerrit on build completion
  """

  if merge_result == orm.StatusKey.SUCCESS.value:
    result_string = 'successful'
  elif merge_result == orm.StatusKey.CANCELED.value:
    result_string = 'cancelled'
  else:
    result_string = 'failed'

  return RESULT_TPL.format(webfront_url, merge_rid, result_string)


def mark_gerrit_change_with_result(gerrit, changeinfo, webfront_url, merge_rid,
                                   merge_result):
  """
  Posts a review to a gerrit change informing owner and reviewers the merge
  succeeded or failed.
  """

  message = get_result_message(webfront_url, merge_rid, merge_result)

  if merge_result == 0:
    label = 1
  else:
    label = -1

  review_dict = {'message': message,
                 'labels': {'Merge-Queue': label}}
  # If the merge succeeds the user will already get an email from gerrit
  # so there's no need to email again
  if merge_result == 0:
    review_dict['notify'] = 'NONE'
  gerrit.set_review(changeinfo.change_id, changeinfo.current_revision,
                    review_dict)


def fetch_branches_from_origin(repo):
    # fetch branches from origin
  logging.info('Fetching branches from origin')
  repo.git.fetch('origin', prune=True)


def merge_a_into_b(repo, branch_a, branch_b):
  logging.info('Checking out %s', branch_b)
  repo.git.checkout(branch_b)
  logging.info('Merging %s into %s', branch_a, branch_b)

  # NOTE(josh): aN is 'author name' and aE is 'author email'.
  author_str = repo.git.show('HEAD', no_patch=True,
                             format="%aN <%aE>").strip()
  author = '"{}"'.format(author_str)

  # NOTE(justin): adding this instrumentation to understand why this merge
  # step sometimes seems to no-op when it should take action.
  target_head = repo.git.show(branch_a, no_patch=True, format="%h").strip()
  logging.info('%s head commit: %s', branch_a, target_head)

  # NOTE(josh): I verified that even with --no-commit specified, the merge
  # exits with error code 1, so this command should raise an exception if the
  # merge is not clean
  repo.git.merge(branch_a, no_commit=True)

  # This is a trick to use the default merge commit message. We set the
  # editor to a command which ignores it's arguments and exits with error
  # code 0. Git will generate a merge message, write it to a temporary file,
  # then call the editor with that file as a command line argument. The
  # command 'true' immediately exits with error code '0' and the commit
  # message is unchanged. Note that 'true' is an actual binary executable
  # in /bin/true on ubuntu 14.04.
  old_env = repo.git.update_environment(GIT_EDITOR='true')

  try:
    # Attempt to commit the merge.  If the merge was a no-op, this commit
    # will fail, and we can just ignore that failure. We pass --no-verify
    # because we don't want to run pre-commit hooks for this merge.
    logging.info('Committing merge')
    repo.git.commit(author=author, no_verify=True)
  except git.exc.GitCommandError:
    # NOTE(justin): This commit seems to be failing sometimes for reasons
    # besides being a no-op, i.e. it's actually a failed commit.  If it really
    # was a no-op we should have  an empty git_status; otherwise re-raise the
    # error we received.
    git_status = repo.git.status(porcelain=True)
    if git_status:
      logging.info('Merge commit failed, but git status is not clean. '
                   'Git status:\n%s', git_status)
      raise
    else:
      logging.info('Merge was a no-op; skipping commit.')

  # Restore the git environment as it was before our little hack.
  repo.git.update_environment(**old_env)


def merge_features_together(repo, merge_branch, change_queue):
  """
  Create a new branch, merge all the changes into it the amature way.
  """
  target_branch = change_queue[0].branch
  logging.info('Checking out target branch %s', target_branch)
  repo.git.checkout(target_branch)

  logging.info('Creating merge branch %s', merge_branch)
  new_branch = repo.create_head(merge_branch)
  new_branch.checkout()

  for changeinfo in change_queue:
    feature_branch = changeinfo.message_meta['Feature-Branch']
    # merge target into feature
    merge_a_into_b(repo, merge_branch, feature_branch)
    # merge updated feature back into target
    merge_a_into_b(repo, feature_branch, merge_branch)


def kill_step(step_proc):
  logging.info('Waiting for build step to die, pid=%d', step_proc.pid)
  start_time = time.time()

  if step_proc.returncode is None:
    logging.info('Signalling with SIGTERM')
  while step_proc.poll() is None:
    step_proc.send_signal(signal.SIGTERM)
    time.sleep(2)
    if time.time() - start_time > 10:
      break

  if step_proc.returncode is None:
    logging.info('Signalling with SIGKILL')

  while step_proc.poll() is None:
    step_proc.send_signal(signal.SIGKILL)
    time.sleep(2)
    if time.time() - start_time > 10:
      break

  logging.info('Build step appears to be zombified. Hopefully it does not'
               'affect future merges')


def mark_old_changes_as_failed(sql):
  """
  If the daemon was killed during a merge, then mark that merge as failed.
  """

  query = (sql.query(orm.MergeStatus)
           .filter(orm.MergeStatus.status == orm.StatusKey.IN_PROGRESS.value))
  for merge_status in query:
    logging.info('Marking stale merge status %d as failed', merge_status.rid)
    merge_status.status = orm.StatusKey.CANCELED.value
  sql.commit()


def submit_changes_with_rest(gerrit, change_queue):
  """
  Submit the list of changes through the gerrit REST api
  """
  for changeinfo in change_queue:
    logging.info('Submitting %s through REST API',
                 changeinfo.change_id)
    # NOTE(josh): on-behalf-of appears to be restricted with our current
    # configuration. Otherwise use changeinfo.owner.account_id or the
    # account_id of whoever supplied the resolved mergequeue score
    response = gerrit.submit_change(changeinfo.change_id)
    if response.get('status') != 'SUBMITTED':
      logging.warn('Gerrit refused to submit the change over REST')
      break


def submit_changes_with_cmd(repo, change_queue, submit_cmd, popen_kwargs):
  """
  Submit the list of changes using a command.
  """
  target_branch = change_queue[0].branch
  for changeinfo in change_queue:
    logging.info('Checking out target branch: %s', target_branch)
    repo.git.checkout(target_branch)

    logging.info('Pulling target branch state from gerrit')
    repo.git.pull()

    feature_branch = changeinfo.message_meta['Feature-Branch']
    merge_a_into_b(repo, target_branch, feature_branch)

    logging.info('Submitting %s through command line',
                 changeinfo.change_id)
    try:
      step_proc = subprocess.Popen(submit_cmd, **popen_kwargs)
    except OSError:
      logging.exception("Failed to execute %s", ' '.join(submit_cmd))
      break

    while step_proc.poll() is None:
      # TODO(josh): timeout here if submit takes too long
      time.sleep(1)


def create_sql_records(sql, queue_spec, change_queue):
  """
  Create a record for the merge including records for each change verified as
  part of this merge verification. Return the main merge record.
  """

  # NOTE(josh): row id will be assigned by the database and retrieved by
  # SQLAlchemy when we commit() below.
  merge = orm.MergeStatus(
      project=queue_spec.project,
      branch=change_queue[0].branch,
      start_time=datetime.datetime.utcnow(),
      end_time=datetime.datetime.utcnow(),
      status=orm.StatusKey.IN_PROGRESS.value)
  sql.add(merge)
  sql.commit()

  for changeinfo in change_queue:
    feature_branch = changeinfo.message_meta.get('Feature-Branch', None)
    if feature_branch is None:
      raise RuntimeError('No Feature-Branch in message for {}'
                         .format(changeinfo.change_id))
    change = orm.MergeChange(
        merge_id=merge.rid,
        change_id=changeinfo.change_id,
        owner_id=changeinfo.owner.account_id,
        feature_branch=feature_branch,
        request_time=changeinfo.queue_time,
        msg_meta=json.dumps(changeinfo.message_meta))
    sql.add(change)
  sql.commit()

  return merge


def run_steps(queue_spec, gerrit, change_queue, sql_session, merge_id,
              popen_kwargs):
  """
  Performs each build, test step.
  """

  logging.info('Performing build/test steps')

  for step_idx, step_cmd in enumerate(queue_spec.build_steps):
    # Reset every step so we check at least once per step
    last_gerrit_poll = 0
    last_db_poll = 0
    last_timing_print = 0
    step_start_time = time.time()

    # Write the command that we are running for this step into the log so we
    # can associate stdout and stderr with the command that was run
    for stream in ['stdout', 'stderr']:
      log = popen_kwargs[stream]
      log.write(STEP_TPL.format(stepno=step_idx, command=' '.join(step_cmd)))
      log.flush()

    try:
      step_proc = subprocess.Popen(step_cmd, **popen_kwargs)
    except OSError:
      logging.exception("Failed to execute %s", ' '.join(step_cmd))
      raise

    command_str = ' '.join(step_cmd)
    logging.info('{} {}'.format(step_idx, command_str))

    gerrit_poll_count = 0
    gerrit_poll_failures = 0

    if queue_spec.submit_with_rest:
      should_poll_gerrit = True
    else:
      if step_idx < len(queue_spec.build_steps) - 1:
        should_poll_gerrit = True
      else:
        # NOTE(josh): if this is the last of the build steps and the merge queue
        # does not submit through the REST api, then this step must do the
        # actual merge somehow (i.e. through the gerrit REST api or through the
        # gerrit command line interface). In this case it may alter the state
        # of the review which may remove the MQ+1 score which we SHOULD NOT
        # interpret as a merge request cancellation.
        should_poll_gerrit = False

    while step_proc.poll() is None:
      # Print a message every two minutes for sanity
      if time.time() - last_timing_print > 5 * 60:
        last_timing_print = time.time()
        step_duration = last_timing_print - step_start_time
        logging.debug('Step %d has been running for %6.2f seconds',
                      step_idx, step_duration)

      # NOTE(josh): check for cancellation on gerrit every 30 seconds
      if should_poll_gerrit and (time.time() - last_gerrit_poll > 30):
        last_gerrit_poll = time.time()
        canceled_ids = []
        try:
          gerrit_poll_count += 1
          canceled_ids = gerrit.get_changes_canceled_on_gerrit(change_queue)
        except (requests.RequestException, ValueError):
          gerrit_poll_failures += 1
          if gerrit_poll_failures == 1:
            logging.exception("Failed to poll gerrit for changes.\n"
                              " NOTE(josh): This is known to happen from time "
                              " to time, so don't be too concerned.")
          else:
            logging.warn('Failed to poll gerrit for changes %d/%d',
                         gerrit_poll_failures, gerrit_poll_count)
          continue

        if canceled_ids:
          logging.info(GERRIT_CANCEL, '\n  '.join(canceled_ids))
          kill_step(step_proc)
          return orm.StatusKey.CANCELED.value

      # NOTE(josh): check for cancellation on mq database
      if time.time() - last_db_poll > 10:
        last_db_poll = time.time()

        query = (sql_session.query(orm.Cancellation)
                 .filter(orm.Cancellation.rid == merge_id))
        for record in query:
          logging.info(WEBFRONT_CANCEL, record.who, record.when)
          kill_step(step_proc)
          return orm.StatusKey.CANCELED.value

      # TODO(josh): check for cancellation, update status/heartbeat,
      # check for timeout, print animation, estimate progess based on lines
      # of output, etc
      time.sleep(1)

    logging.info('{} {} [{}] '.format(step_idx, command_str,
                                      step_proc.returncode))
    if step_proc.returncode != 0:
      message = FAILURE_TPL.format(stepno=step_idx,
                                   retcode=step_proc.returncode,
                                   command=command_str)
      raise RuntimeError(message)

  return orm.StatusKey.SUCCESS.value


def cleanup_repo(repo):
  """
  Cleanup branches and working tree after merge
  """
  # clean up repo in case anything failed
  repo.git.reset('--hard')
  # -f is 'force', -d is 'remove whole directories'
  repo.git.clean('-fd')
  repo.git.checkout('master')
  repo.git.clean('-fd')

  # delete all branches except master
  for branch in repo.git.branch().split():
    if branch != '*' and branch.strip() != 'master':
      logging.info('Deleting left-over branch %s', branch)
      try:
        repo.git.branch('-D', branch)
      except git.exc.GitCommandError:
        logging.exception(
            'Failed to delete leftover feature branch %s', branch)


def get_or_clone_repo(config, repo_path, project):
  try:
    return git.Repo(repo_path)
  except (git.NoSuchPathError, git.InvalidGitRepositoryError):
    logging.exception('workspace does not appear to be a git repository: %s',
                      repo_path)

  try:
    os.makedirs(os.path.dirname(repo_path))
  except OSError:
    pass

  # clone the repository if needed
  repository_url = 'ssh://{}@{}:{}/{}.git'.format(
      config['gerrit.ssh.username'],
      config['gerrit.ssh.host'],
      config['gerrit.ssh.port'],
      project)
  logging.info('attemping to clone %s', repository_url)

  # TODO(josh): replace with subprocess call, gitpython appears to supress
  # the command output which would be pretty handy for sanity sake
  git.Repo.clone_from(repository_url, repo_path)

  # if this is a fresh clone we need to run git-fat init, so do it every time
  # anyway
  subprocess.call(['git-fat', 'init'], cwd=repo_path)

  # create a git repository object for this working repository
  return git.Repo(repo_path)


def handle_pid_file(pidfile_path):
  """
  Verify that we are the only daemon running by writing a PID file. If the
  file already exists read it to see what other daemon PID file is running.
  If that pid is no longer active assume it has died and we are allowed to
  start.
  """

  try:
    os.makedirs(os.path.dirname(pidfile_path))
  except OSError:
    pass

  other_pid = None
  try:
    with open(pidfile_path, 'r') as infile:
      other_pid = int(infile.read().strip())
  except (OSError, IOError, ValueError):
    pass

  # NOTE(josh): if we restart due to file change then this file will exist
  # but it will contain our pid.
  if other_pid is not None and other_pid != os.getpid():
    if os.path.exists('/proc/{}/stat'.format(other_pid)):
      logging.error('Another daemon is already running with pid %d.',
                    other_pid)
      return 1
    else:
      logging.warn('Daemon pid file %s exists containing pid %d which is not'
                   ' alive, will overwrite', pidfile_path, other_pid)

  with open(pidfile_path, 'w') as pidfile:
    pidfile.write('{}\n'.format(os.getpid()))


def get_requests_matching(request_queue, project, branch):
  """
  Filter request_queue returning a list of only those requests matching the
  given branch and project
  """

  return [cinfo for cinfo in request_queue
          if cinfo.project == project and cinfo.branch == branch]


def get_requests_from_single_queue(request_queue, queue_specs):
  """
  Find the first merge request in `request_queue` that matches a specification
  in `queue_specs`. Then, given that `queue_spec`, build and return a list of
  all outstanding changes to that (`project`, `branch`).
  """
  for cinfo in request_queue:
    if cinfo.project in queue_specs:
      for spec in queue_specs[cinfo.project]:
        if spec.branch.match(cinfo.branch):
          return spec, get_requests_matching(request_queue, cinfo.project,
                                             cinfo.branch)
  return None, []


class LogInfo(object):

  def __init__(self):
    self.app_logpath = None
    self.log_handler = None
    self.stdout_logpath = None
    self.stdout = None
    self.stderr_logpath = None
    self.stderr = None


def setup_logs(log_path, merge_id):
  """
  Open three log files for app, stdout, and stderr.
  """

  # Create a temporary logging handler which copies log events to the named
  # log file for this merge
  app_logpath = '{}/{:06d}.log'.format(log_path, merge_id)
  log_handler = logging.FileHandler(app_logpath, 'w')
  log_handler.setLevel(logging.DEBUG)
  logging.getLogger('').addHandler(log_handler)

  # Create log files for stdout and stderr of build steps
  stdout_logpath = '{}/{:06d}.stdout'.format(log_path, merge_id)
  stdout_log = open(stdout_logpath, 'w')

  stderr_logpath = '{}/{:06d}.stderr'.format(log_path, merge_id)
  stderr_log = open(stderr_logpath, 'w')

  out = LogInfo()
  out.app_logpath = app_logpath
  out.log_handler = log_handler
  out.stdout = stdout_log
  out.stdout_logpath = stdout_logpath
  out.stderr = stderr_log
  out.stderr_logpath = stderr_logpath

  return out


class MergeDaemon(object):

  def __init__(self, config, gerrit, sql_session):
    self.config = config
    self.gerrit = gerrit
    self.sql_session = sql_session

    try:
      os.makedirs(config['daemon.workspace_path'])
    except OSError:
      pass

    queue_index = {}
    for spec_dict in self.config['queues']:
      spec = QueueSpec(**spec_dict)
      queue_index[(spec.project, spec.name)] = spec

    self.queues = {}
    for project, name in self.config['daemon.queues']:
      spec = queue_index.get((project, name), None)
      if spec is None:
        logging.warn('daemon queue not listed in queue index: %s/%s',
                     project, name)
        continue
      if project not in self.queues:
        self.queues[project] = []
      self.queues[project].append(spec)

    # increase ccache size
    sub_env = os.environ.copy()
    sub_env['CCACHE_DIR'] = self.config['daemon.ccache.path']

    try:
      os.makedirs(config['daemon.ccache.path'])
    except OSError:
      pass

    subprocess.check_call(['ccache', '-M', config['daemon.ccache.size']],
                          env=sub_env, cwd=config['daemon.workspace_path'])

  def coalesce_merge(self, queue_spec, change_queue):
    """
    Merge all changes from `change_queue` together, verify the build and, if
    it passes, then submit all of the changes through gerrit.
    """

    # Take this opportunity to to update the AccountInfo table with any new
    # owner info contained in this change
    for changeinfo in change_queue:
      functions.add_or_update_account_info(self.sql_session,
                                           changeinfo.owner)
      self.sql_session.commit()

    # Create a log entry for this merge attempt. Note that the id will be
    # assigned by sqlalchemy after we 'commit' to the database.
    merge = create_sql_records(self.sql_session, queue_spec, change_queue)

    silent = self.config.get('daemon.silent', False)
    logctx = setup_logs(self.config['log_path'], merge.rid)

    logging.info('Starting verification of the following changes: \n  %s',
                 '\n  '.join([changeinfo.change_id for changeinfo
                              in change_queue]))
    repo = None
    try:
      repo_path = queue_spec.get_workspace(self.config['daemon.workspace_path'])
      repo = get_or_clone_repo(self.config, repo_path=repo_path,
                               project=queue_spec.project)

      if not silent:
        message = IN_SUBMISSION_TPL.format(self.config['webfront.url'],
                                           merge.rid)
        review_dict = {'message': message,
                       'labels': {'Merge-Queue': 0},
                       'notify': 'NONE'}  # don't email on merge started
        for changeinfo in change_queue:
          self.gerrit.set_review(changeinfo.change_id,
                                 changeinfo.current_revision, review_dict)

      fetch_branches_from_origin(repo)
      merge_branch = 'mergequeue_{:06d}'.format(merge.rid)
      merge_features_together(repo, merge_branch, change_queue)

      if not silent:
        # Push the updated feature branch back to origin so its state there is
        # up to date.
        repo.git.push('origin', '{0}:{0}'.format(merge_branch), force=True)

      popen_kwargs = {
          'env': queue_spec.get_environment(self.config),
          'cwd': queue_spec.get_workspace(self.config['daemon.workspace_path']),
          'stdout': logctx.stdout,
          'stderr': logctx.stderr,
      }

      merge.status = run_steps(queue_spec, self.gerrit, change_queue,
                               self.sql_session, merge.rid, popen_kwargs)

      if not silent:
        repo.git.push('origin', ':{}'.format(merge_branch))

    except (OSError, RuntimeError, KeyError, git.exc.GitCommandError):
      merge.status = orm.StatusKey.STEP_FAILED.value
      logging.exception('Exception caught during merge')

    if repo is not None:
      cleanup_repo(repo)

    if merge.status == orm.StatusKey.SUCCESS.value:
      if queue_spec.submit_with_rest:
        submit_changes_with_rest(self.gerrit, change_queue)
      else:
        cleanup_repo(repo)
        submit_changes_with_cmd(repo, change_queue, queue_spec.submit_cmd,
                                popen_kwargs)

    # Add a comment to gerrit indicating success or failure, and setting a
    # review score for the Merge-Queue label.
    if not silent:
      message = get_result_message(self.config['webfront.url'], merge.rid,
                                   merge.status)

      # NOTE(josh): if this is the second pass, then we want the label to
      # be -1: on failure
      review_score = 0
      if (len(change_queue) == 1
          and merge.status != orm.StatusKey.SUCCESS.value):
        review_score = -1

      review_dict = {'message': message,
                     'labels': {'Merge-Queue': review_score}}

      # If the merge succeeds the user will already get an email from gerrit
      # so there's no need to email again.
      if merge.status == orm.StatusKey.SUCCESS.value:
        review_dict['notify'] = 'NONE'

      for changeinfo in change_queue:
        self.gerrit.set_review(changeinfo.change_id,
                               changeinfo.current_revision, review_dict)

    # mark the time when the merge was completed / failed
    merge.end_time = datetime.datetime.utcnow()

    # commit change to history database
    self.sql_session.commit()

    # remove the the handler that is logging messages to the file for this merge
    logging.getLogger('').removeHandler(logctx.log_handler)
    logctx.log_handler.close()
    logctx.stdout.close()
    logctx.stderr.close()

    # compress the logs
    for logpath in [logctx.app_logpath, logctx.stdout_logpath,
                    logctx.stderr_logpath]:
      subprocess.call(['gzip', '--force', logpath])

      # NOTE(josh): the nginx gzip_static module wants the original files around
      # or it wont serve the compressed ones. It's not great to rely on nginx
      # behavior but for now we touch the file to make nginx happy and then
      # hope that it wont be served.
      with open(logpath, 'w') as _:
        pass

    if merge.status == orm.StatusKey.SUCCESS.value:
      for changeinfo in change_queue:
        queue_spec.dirty_changes.discard(changeinfo.change_id)
      return 0
    else:
      for changeinfo in change_queue:
        queue_spec.dirty_changes.add(changeinfo.change_id)
      return -1

  def run(self, watch_manifest):
    pidfile_path = self.config.get('daemon.pidfile_path', './pid')
    handle_pid_file(pidfile_path)
    poll_period = self.config.get('daemon.poll_period', 60)
    offline_sentinel_path = self.config.get('daemon.offline_sentinel_path',
                                            './pause')

    mark_old_changes_as_failed(self.sql_session)
    last_poll_time = 0

    while True:
      functions.restart_if_modified(watch_manifest, pidfile_path)

      try:
        if os.path.exists(offline_sentinel_path):
          logging.info('Offline sentinal exists, bypassing merges')
          while os.path.exists(offline_sentinel_path):
            functions.restart_if_modified(watch_manifest, pidfile_path)
            time.sleep(1)
          logging.info('Offline sentinel removed, continuing')
          continue

        # If the loop was faster than poll period, then wait for
        # the remainder of the period to prevent spamming gerrit
        loop_duration = time.time() - last_poll_time
        backoff_duration = poll_period - loop_duration
        if backoff_duration > 0:
          logging.info('Loop was very fast, waiting for '
                       '%6.2f seconds', backoff_duration)
          time.sleep(backoff_duration)

        last_poll_time = time.time()
        poll_id = functions.get_next_poll_id(self.sql_session)
        functions.poll_gerrit(self.gerrit, self.sql_session, poll_id)
        _, global_queue = functions.get_queue(self.sql_session)

        queue_spec, request_queue = \
            get_requests_from_single_queue(global_queue, self.queues)

        if queue_spec is None or not request_queue:
          # If there are no changes to any of the queues that this daemon is
          # monitoring then we have nothing to do here.
          continue

        if queue_spec.coalesce_count > 0 and len(request_queue) > 1:
          # NOTE(josh): Only coalesce changes that have never failed
          # verification before.
          coalesce_queue = []
          for changeinfo in request_queue:
            if changeinfo.change_id in queue_spec.dirty_changes:
              logging.info('ceasing merge colation since %s is dirty',
                           changeinfo.change_id)
              break
            else:
              coalesce_queue.append(changeinfo)
            if len(coalesce_queue) >= queue_spec.coalesce_count:
              break

          if len(coalesce_queue) > 1:
            result = self.coalesce_merge(queue_spec, coalesce_queue)
            if result == 0:
              # The coalition of changes was verified together, they have all
              # been merged so we can poll gerrit and move on to more changes.
              continue
            else:
              for changeinfo in coalesce_queue:
                queue_spec.dirty_changes.add(changeinfo.change_id)
          else:
            logging.info('falling back to single-merge since coalition '
                         'contains only one clean change')
        else:
          logging.info('skipping merge coalition, coalesce_count: %d, '
                       'len(request_queue): %d', queue_spec.coalesce_count,
                       len(request_queue))

        # NOTE(josh): only do one merge per request to gerrit so that
        # any changes to the queue (i.e. gerrit state through review
        # updates or priority changes) are reflected in the merge order,
        # as well as allowing us to pick-up on the pause sentinel
        self.coalesce_merge(queue_spec, request_queue[:1])
        queue_spec.dirty_changes.discard(request_queue[0].change_id)

      except (httplib2.HttpLib2Error, requests.RequestException):
        logging.exception('Error retrieving merge requests from gerrit')
        continue

      except KeyboardInterrupt:
        break

    logging.info('Exiting main loop')

    return 0
