"""
Merge-Queue daemon, repeatedly polls gerrit for any merge requests, and merges
them in serial order.
"""

import datetime
import git
import json
import logging.handlers
import os
import re
import signal
import subprocess
import time

import httplib2
import requests
from gerrit_mq import orm

IN_SUBMISSION_TPL = """
Gerrit Merge-Queue has started to merge this change as merge #{1}.
{0}/detail.html?merge_id={1}
"""

RESULT_TPL = """
Merge #{1} {2}.
{0}/detail.html?merge_id={1}
"""

FAILURE_TPL = """
-------------------------
Merge failed on step {stepno}. The following command exited with nonzero status:
{command}

The return code was {retcode}
-------------------------
"""

STEP_TPL = """
-------------------------
Executing step: {stepno}
{command}
-------------------------
"""


class QueueSpec(object):
  """
  Specification of a single queue of serialized merges
  """

  def __init__(self, project, branch, build_env, build_steps, name=None,
               merge_build_env=False, submit_with_rest=True):
    self.project = project
    self.branch = re.compile(branch)
    self.build_env = dict(build_env)
    self.build_steps = [list(step) for step in build_steps]

    if name is None:
      assert re.escape(branch) == branch
      self.name = branch
    else:
      self.name = name

    self.merge_build_env = merge_build_env
    self.submit_with_rest = submit_with_rest

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


def mark_gerrit_change_with_result(gerrit, changeinfo, webfront_url, merge_rid,
                                   merge_result):
  """
  Posts a review to a gerrit change informing owner and reviewers the merge
  succeeded or failed.
  """

  if merge_result == 0:
    result_string = 'successful'
  else:
    result_string = 'failed'
  message = RESULT_TPL.format(webfront_url, merge_rid, result_string)

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


def merge_target_into_feature(repo, target_branch, feature_branch):
  logging.info('Checking out feature branch %s', feature_branch)
  repo.git.checkout(feature_branch)
  logging.info('Merging target into current branch')

  # NOTE(josh): aN is 'author name' and aE is 'author email'.
  author_str = repo.git.show('HEAD', no_patch=True,
                             format="%aN <%aE>").strip()
  author = '"{}"'.format(author_str)

  # NOTE(justin): adding this instrumentation to understand why this merge
  # step sometimes seems to no-op when it should take action.
  qualified_branch_name = 'origin/{}'.format(target_branch)
  target_head = repo.git.show(qualified_branch_name,
                              no_patch=True, format="%h").strip()
  logging.info('Current target head commit: %s', target_head)

  # NOTE(josh): I verified that even with --no-commit specified, the merge
  # exits with error code 1, so this command should raise an exception if the
  # merge is not clean
  repo.git.merge('origin/{}'.format(target_branch), no_commit=True)
  logging.info('Committing merge')

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


def add_or_update_account_info(sql, ai_obj):
  """
  Update the AccountInfo object from gerrit json, or create it if it's new.
  """
  query = (sql
           .query(orm.AccountInfo)
           .filter(orm.AccountInfo.rid == ai_obj.account_id))
  if query.count() > 0:
    for ai_sql in query:
      for field in ['name', 'email', 'username']:
        setattr(ai_sql, field, getattr(ai_obj, field))
      sql.commit()
      return
  else:
    kwargs = ai_obj.as_dict()
    kwargs['rid'] = kwargs.pop('_account_id')
    for key in ['name', 'email', 'username']:
      if key not in kwargs:
        kwargs[key] = '<none>'
    ai_sql = orm.AccountInfo(**kwargs)
    sql.add(ai_sql)
    sql.commit()


def mark_old_changed_as_failed(sql):
  """
  If the daemon was killed during a merge, then mark that merge as failed.
  """

  query = (sql.query(orm.MergeStatus)
           .filter(orm.MergeStatus.status == orm.StatusKey.IN_PROGRESS.value))
  for merge_status in query:
    logging.info('Marking stale merge status %d as failed', merge_status.rid)
    merge_status.status = orm.StatusKey.CANCELED.value
  sql.commit()


GERRIT_CANCEL = """
****************
Merge was canceled on gerrit by score removal
****************
"""

WEBFRONT_CANCEL = """
****************
Merge was canceled through the webfront
  by: %s
  on: %s
****************
"""

def run_steps(queue_spec, config, stdout_log, stderr_log,
              gerrit, change_id, sql_session, merge_id):
  """
  Performs each build, test step.
  """

  logging.info('Performing build/test steps')
  popen_kwargs = {
      'env': queue_spec.get_environment(config),
      'cwd': queue_spec.get_workspace(config['daemon.workspace_path']),
      'stdout': stdout_log,
      'stderr': stderr_log,
  }

  for step_idx, step_cmd in enumerate(queue_spec.build_steps):
    # Reset every step so we check at least once per step
    last_gerrit_poll = 0
    last_db_poll = 0
    last_timing_print = 0
    step_start_time = time.time()

    # Write the command that we are running for this step into the log so we
    # can associate stdout and stderr with the command that was run
    for log in [stdout_log, stderr_log]:
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
      if time.time() - last_timing_print > 5*60:
        last_timing_print = time.time()
        step_duration = last_timing_print - step_start_time
        logging.debug('Step %d has been running for %6.2f seconds',
                      step_idx, step_duration)

      # NOTE(josh): check for cancellation on gerrit every 30 seconds
      if should_poll_gerrit and (time.time() - last_gerrit_poll > 30):
        last_gerrit_poll = time.time()
        changeinfo = None
        try:
          gerrit_poll_count += 1
          changeinfo = gerrit.get_change(change_id)
        except (requests.RequestException, ValueError):
          gerrit_poll_failures += 1
          if gerrit_poll_failures == 1:
            logging.exception("Failed to poll changeinfo for change %s.\n"
                              " NOTE(josh): This is known to happen from time "
                              " to time, so don't be too concerned.", change_id)
          else:
            logging.warn('Failed to poll changeinfo for change %s %d/%d',
                         change_id, gerrit_poll_failures, gerrit_poll_count)
          continue

        if changeinfo is not None and changeinfo.queue_score != 1:
          logging.info(GERRIT_CANCEL)
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


  def merge_change(self, queue_spec, changeinfo):
    """
    Attempt to merge the requested change.
    """

    # Create a log entry for this merge attempt. Note that the id will be
    # assigned by sqlalchemy after we 'commit' to the database.
    merge = orm.MergeStatus(
        project=changeinfo.project,
        branch=changeinfo.branch,
        change_id=changeinfo.change_id,
        owner_id=changeinfo.owner.account_id,
        request_time=changeinfo.queue_time,
        start_time=datetime.datetime.utcnow(),
        end_time=datetime.datetime.utcnow(),
        status=orm.StatusKey.IN_PROGRESS.value,
        msg_meta=json.dumps(changeinfo.message_meta))
    self.sql_session.add(merge)
    self.sql_session.commit()

    silent = self.config.get('daemon.silent', False)

    # Create a temporary logging handler which copies log events to the named
    # log file for this merge
    app_logpath = '{}/{:06d}.log'.format(self.config['log_path'],
                                         merge.rid)
    log_handler = logging.FileHandler(app_logpath, 'w')
    log_handler.setLevel(logging.DEBUG)
    logging.getLogger('').addHandler(log_handler)

    # Create log files for stdout and stderr of build steps
    stdout_logpath = '{}/{:06d}.stdout'.format(self.config['log_path'],
                                               merge.rid)
    stdout_log = open(stdout_logpath, 'w')

    stderr_logpath = '{}/{:06d}.stderr'.format(self.config['log_path'],
                                               merge.rid)
    stderr_log = open(stderr_logpath, 'w')

    logging.info('Starting merge')
    logging.info(changeinfo.pretty_string())

    repo = None
    try:
      repo_path = queue_spec.get_workspace(self.config['daemon.workspace_path'])
      repo = get_or_clone_repo(self.config, repo_path=repo_path,
                               project=queue_spec.project)

      if not silent:
        mark_gerrit_change_as_in_submission(self.gerrit, changeinfo,
                                            self.config['webfront.url'],
                                            merge.rid)

      feature_branch = changeinfo.message_meta.get('Feature-Branch', None)
      if feature_branch is None:
        raise RuntimeError('No Feature-Branch in message')

      merge.feature_branch = feature_branch
      self.sql_session.commit()

      fetch_branches_from_origin(repo)
      merge_target_into_feature(repo, merge.branch,
                                merge.feature_branch)

      if not silent:
        # Push the updated feature branch back to origin so its state there is
        # up to date.
        repo.git.push()

      merge.status = run_steps(queue_spec, self.config, stdout_log, stderr_log,
                               self.gerrit, merge.change_id, self.sql_session,
                               merge.rid)
    except (OSError, RuntimeError, KeyError, git.exc.GitCommandError):
      merge.status = orm.StatusKey.STEP_FAILED.value
      logging.exception('Exception caught during merge')

    if repo is not None:
      cleanup_repo(repo)

    if (queue_spec.submit_with_rest and
        merge.status == orm.StatusKey.SUCCESS.value):
      # NOTE(josh): on-behalf-of appears to be restricted with our current
      # configuration. Otherwise use changeinfo.owner.account_id or the
      # account_id of whoever supplied the resolved mergequeue score
      response = self.gerrit.submit_change(changeinfo.change_id)
      if response.get('status') == 'SUBMITTED':
        logging.info('Gerrit refused to submit the change over REST')
        merge.status = orm.StatusKey.SUCCESS.value
      else:
        merge.status = orm.StatusKey.STEP_FAILED.value

    # Add a comment to the gerrit indicating success or failure, and setting a
    # review score for the Merge-Queue label.
    try:
      if not silent:
        mark_gerrit_change_with_result(self.gerrit, changeinfo,
                                       self.config['webfront.url'],
                                       merge.rid, merge.status)
    except RuntimeError:
      logging.warn("Failed to set result of merge in gerrit review")

    # mark the time when the merge was completed / failed
    merge.end_time = datetime.datetime.utcnow()

    # commit change to history database
    self.sql_session.commit()

    # remove the the handler that is logging messages to the file for this merge
    logging.getLogger('').removeHandler(log_handler)
    log_handler.close()
    stdout_log.close()
    stderr_log.close()

    # compress the logs
    for logpath in [app_logpath, stdout_logpath, stderr_logpath]:
      subprocess.call(['gzip', '--force', logpath])

      # NOTE(josh): the nginx gzip_static module wants the original files around
      # or it wont serve the compressed ones. It's not great to rely on nginx
      # behavior but for now we touch the file to make nginx happy and then
      # hope that it wont be served.
      with open(logpath, 'w') as _:
        pass

    return

  def run(self):
    pidfile_path = self.config.get('daemon.pidfile_path', './pid')
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

    if other_pid is not None:
      if os.path.exists('/proc/{}/stat'.format(other_pid)):
        logging.error('Another daemon is already running with pid %d.',
                      other_pid)
        return 1
      else:
        logging.warn('Daemon pid file %s exists containing pid %d which is not'
                     ' alive, will overwrite', pidfile_path, other_pid)

    with open(pidfile_path, 'w') as pidfile:
      pidfile.write('{}\n'.format(os.getpid()))

    poll_period = self.config.get('daemon.poll_period', 60)
    offline_sentinel_path = self.config.get('daemon.offline_sentinel_path',
                                            './pause')

    mark_old_changed_as_failed(self.sql_session)

    while True:
      try:
        if os.path.exists(offline_sentinel_path):
          logging.info('Offline sentinal exists, bypassing merges')
          while os.path.exists(offline_sentinel_path):
            time.sleep(poll_period)
          logging.info('Offline sentinel removed, continuing')
          continue
        else:
          request_queue = self.gerrit.get_merge_requests()
          performed_a_merge = False
          for changeinfo in request_queue:
            if changeinfo.project in self.queues:
              # Take this opportunity to to update the AccountInfo table
              # with the owner info
              add_or_update_account_info(self.sql_session, changeinfo.owner)

              for spec in self.queues[changeinfo.project]:
                if spec.branch.match(changeinfo.branch):
                  time_before_merge = time.time()
                  self.merge_change(spec, changeinfo)

                  # If the merge was faster than poll period, then wait for
                  # the remainder of the period to prevent spamming gerrit
                  merge_duration = time.time() - time_before_merge
                  backoff_duration = poll_period - merge_duration
                  if backoff_duration > 0:
                    logging.info('Merge was very fast, waiting for '
                                 '%6.2f seconds', backoff_duration)
                    time.sleep(backoff_duration)

                  performed_a_merge = True
                  break

            # NOTE(josh): only do one merge per request to gerrit so that
            # any changes to the queue (i.e. gerrit state through review
            # updates or priority changes) are reflected in the merge order,
            # as well as allowing us to pick-up on the pause sentinel
            if performed_a_merge:
              break

          # If there are no changes, sleep for a little bit before hammering
          # the gerrit API
          if not performed_a_merge:
            time.sleep(poll_period)
          continue

      except (httplib2.HttpLib2Error, requests.RequestException):
        logging.exception('Error retrieving merge requests from gerrit')
        time.sleep(poll_period)
        continue

      except KeyboardInterrupt:
        break

    logging.info('Exiting main loop')
    os.remove(pidfile_path)
    return 0
