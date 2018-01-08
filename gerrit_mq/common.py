"""Provides common functionality for unattended merge tools."""

import datetime
import json
import logging
import urllib

import pygerrit2.rest
import requests


class ConfigDict(dict):
  """
  Wraps a nested native python dictionary with semantics for accessing
  nested elements through decimal delimited paths.
  """

  def __getitem__(self, key):
    if (isinstance(key, str) or isinstance(key, unicode)) and '.' in key:
      path = key.split('.')
      sub = self
      for part in path:
        sub = sub[part]
      return sub
    else:
      return super(ConfigDict, self).__getitem__(key)

  def __setitem__(self, key, value):
    if (isinstance(key, str) or isinstance(key, unicode)) and '.' in key:
      path = key.split('.')
      sub = self
      for part in path[:-1]:
        if part not in sub:
          sub[part] = {}
        sub = sub[part]
      return sub.__setitem__(path[-1], value)
    else:
      return super(ConfigDict, self).__setitem__(key, value)

  def __contains__(self, key):
    if (isinstance(key, str) or isinstance(key, unicode)) and '.' in key:
      path = key.split('.')
      sub = self
      for part in path:
        if part in sub:
          sub = sub[part]
        else:
          return False
      return True
    else:
      return super(ConfigDict, self).__contains__(key)

  def get(self, key, default=None):
    if (isinstance(key, str) or isinstance(key, unicode)) and '.' in key:
      path = key.split('.')
      sub = self
      for part in path:
        if part in sub:
          sub = sub[part]
        else:
          return default
      return sub
    else:
      return super(ConfigDict, self).get(key, default)


def is_valid_changeinfo(json_dict):
  """
  Simple sanity check on a ChangeInfo object. Ensures that it at least
  contains enough information to do our job.
  """
  for key in ['id', 'branch', 'change_id', 'owner', 'updated',
              'current_revision', 'status', 'subject']:
    if key not in json_dict:
      return False
  return True


GERRIT_TIME_SHORT_FMT = '%Y-%m-%d %H:%M:%S'
GERRIT_TIME_FMT = '%Y-%m-%d %H:%M:%S.%f'


def sort_merge_queue_labels(label_entries):
  """
  Given a list of label entries, find all the entries where a merge queue
  score was added, parse the date for that label entry, then sort them
  by date.

  Returns a list of (time, score) tuples.
  """

  timestamped_list = []
  for label_entry in label_entries:
    # if a user sets the value of the Merge-Queue label to 0 the date is
    # removed
    if 'date' not in label_entry:
      continue
    if 'value' not in label_entry:
      continue

    # strip trailing zeros so that strptime doesn't complain
    label_date_str = label_entry['date'].rstrip('0')

    # but if we've stripped all the way to the dot, we've gone too far,
    # and strptime will complain
    if label_date_str.endswith('.'):
      label_date_str += '0'

    label_time = datetime.datetime.strptime(label_date_str, GERRIT_TIME_FMT)
    timestamped_list.append((label_time, label_entry['value']))

  return sorted(timestamped_list)


def get_resolved_merge_queue_score(sorted_labels):
  """
  Given a list of (time, label) pairs, find earliest '+1' after the latest '-1',
  if it exists, and return (time, label). Otherwise return (now, '-1').
  """

  resolved_time = datetime.datetime.utcnow()
  resolved_score = -1

  for mq_time, mq_score in sorted_labels:
    if mq_score == -1:
      resolved_time = mq_time
      resolved_score = mq_score
    elif mq_score == 1 and resolved_score != 1:
      resolved_time = mq_time
      resolved_score = mq_score

  return resolved_time, resolved_score


def gerrit_query(filters):
  """
  Format a query string given gerrit query filters. The query string is
  composed of url-encoded space ('+') separated key:value pairs. The value
  will be quoted if it contains whitespace like key:"value x".
  """
  pairs = []
  for key, value in sorted(filters):
    value = urllib.quote_plus(value)
    if '+' in value:
      pairs.append('{}:"{}"'.format(key, value))
    else:
      pairs.append('{}:{}'.format(key, value))
  return '+'.join(pairs)


class AccountInfo(object):  # pylint: disable=no-init
  """
  Information about a gerrit user
  """

  def __init__(self, _account_id, username, name, email):
    self.account_id = _account_id
    self.username = username
    self.name = name
    self.email = email

  def as_dict(self):
    result = {key: getattr(self, key) for key
              in ['name', 'email', 'username']}
    result['_account_id'] = self.account_id
    return result


class ChangeInfo(object):
  """
  Information about a gerrit change
  """

  def __init__(self, project, branch,  # pylint: disable=unused-argument
               change_id, subject, current_revision, owner, queue_time,
               queue_score, message_meta=None, **kwargs):
    self.project = project
    self.branch = branch
    self.change_id = change_id
    self.subject = subject
    self.current_revision = current_revision
    self.owner = AccountInfo(**owner)
    if message_meta is None:
      self.message_meta = {}
    else:
      self.message_meta = dict(message_meta)
    self.queue_time = queue_time
    self.queue_score = queue_score

  def as_dict(self):
    result = {key: getattr(self, key) for key
              in ['project', 'branch', 'subject', 'current_revision', 'owner',
                  'message_meta', 'change_id']}
    result['owner'] = self.owner.as_dict()
    result['queue_time'] = self.queue_time.strftime(GERRIT_TIME_SHORT_FMT)
    return result

  def pretty_string(self):
    try:
      import yaml
      return yaml.safe_dump(self.as_dict(), indent=2, width=80)
    except ImportError:
      return json.dumps(self.as_dict(), indent=2, sort_keys=True)

  @staticmethod
  def key(changeinfo):

    return (changeinfo.message_meta.get('Priority', 100),
            changeinfo.queue_time, changeinfo.project,
            changeinfo.change_id)


class GerritRest(pygerrit2.rest.GerritRestAPI):
  """
  Provides a high-level interface to the Gerrit REST API for the few specific
  queries we need. This is shared between the unattende merge daemon and the
  web front-end so that the web front-end see's the same queue as the daemon.
  """

  def __init__(self, url, username, password,
               disable_ssl_certificate_validation=False):
    auth = requests.auth.HTTPDigestAuth(username, password)
    verify = (not disable_ssl_certificate_validation)
    super(GerritRest, self).__init__(url=url, auth=auth, verify=verify)

  def get_merge_requests(self, offset=0, limit=25, filters=None):
    """
    Call out the gerrit REST API and return a list of all changes that are
    marked as being requested for merge, in the correct queue-order.

    filters : a list of (key, value) pairs to filter for. For instance
              [('project', 'project_foo')] would limit the query to only
              'project_foo'.

    The following filters are automatically appended:
      * status:new
      * label:code-review=+2
      * label:merge-queue=+1

    returns: a list of (queue_time, changinfo) pairs
    """

    # this is the query that we send to gerrit. We want a list of all changes
    # which are
    #   1) 'NEW' status
    #   2) have been code-reviewed with +2
    #   3) have been labeled with merge-queue +1
    # NOTE(josh): status: 'open' include 'new' and 'submitted', where
    # 'submitted' means that gerrit has not yet merged it. We want to exclude
    # 'submitted'.
    if filters is None:
      filters = []

    filters += [('status', 'new'),
                ('label', 'code-review=+2'),
                ('label', 'merge-queue=+1')]

    # these are extra outputs that we want as part of the query. For each
    # change we want a list of labels that have been assigned, as well as the
    # current revision for the change. The current revision is required for
    # the follow-up query to get the feature branch name
    query_string = ('q=' + gerrit_query(filters) + '&'
                    + urllib.urlencode([('o', 'CURRENT_REVISION'),
                                        ('o', 'LABELS'),
                                        ('o', 'DETAILED_LABELS'),
                                        ('o', 'DETAILED_ACCOUNTS'),
                                        ('start', offset),
                                        ('n', limit)]))

    try:
      parsed_changes = self.get('changes/?' + query_string)
    except (requests.RequestException, ValueError):
      logging.exception('Failed to query queue from gerrit.')
      logging.error('query was:\n' + query_string)
      return []

    changeinfo_list = []
    for json_dict in parsed_changes:
      if not is_valid_changeinfo(json_dict):
        logging.error('Invalid JSON ChangeInfo:')
        logging.error(json.dumps(json_dict, sort_keys=True, indent=2,
                                 separators=(',', ': ')))
        continue

      mq_labels = (json_dict
                   .get('labels', {})
                   .get('Merge-Queue', {})
                   .get('all', []))

      sorted_labels = sort_merge_queue_labels(mq_labels)
      queue_time, queue_score = get_resolved_merge_queue_score(sorted_labels)

      if queue_score == 1:
        commit_message_meta = self.get_message_meta(
            json_dict['change_id'], json_dict['current_revision'])
        json_dict['message_meta'] = commit_message_meta
        json_dict['queue_time'] = queue_time
        json_dict['queue_score'] = queue_score
        changeinfo_list.append(ChangeInfo(**json_dict))
      else:
        logging.info('Skipping change %s with resolved '
                     'Merque-Queue label of %d',
                     json_dict['change_id'], queue_score)

    if len(changeinfo_list) == 0:
      return []

    # NOTE(josh): Gerrit returns the results most-recently-touched-first, so
    # we sort by the timestamp of the earliest +1 after the latest -1 on that
    # change
    return sorted(changeinfo_list, key=ChangeInfo.key)

  def get_changeinfo(self, change_id):
    """
    Return the ChangeInfo object for a particular change id
    """

    query = urllib.urlencode([('o', 'CURRENT_REVISION'),
                              ('o', 'LABELS'),
                              ('o', 'DETAILED_LABELS'),
                              ('o', 'DETAILED_ACCOUNTS')])
    query_uri = 'changes/{}?{}'.format(change_id, query)
    json_dict = self.get(query_uri)

    if not is_valid_changeinfo(json_dict):
      logging.error('Invalid JSON ChangeInfo:')
      logging.error(json.dumps(json_dict, sort_keys=True, indent=2,
                               separators=(',', ': ')))
      return None
    return json_dict

  def get_change(self, change_id):
    json_dict = self.get_changeinfo(change_id)

    mq_labels = (json_dict
                 .get('labels', {})
                 .get('Merge-Queue', {})
                 .get('all', []))
    sorted_labels = sort_merge_queue_labels(mq_labels)
    queue_time, queue_score = get_resolved_merge_queue_score(sorted_labels)

    json_dict['queue_time'] = queue_time
    json_dict['queue_score'] = queue_score
    return ChangeInfo(**json_dict)

  def get_message_meta(self, change_id, revision):
    """
    Call out to gerrit REST API to get the commit message for the most recent
    revision of a a change, and then scan the commit message for the metadata
    tags. Return a dictionary of metadata found.

    The following metadata keys are handled specially:
      * Closes
      * Resolves

    They may be written more than once in the commit message and the contents
    will be merged. The contents are expected to be a comma separated list of
    strings. The output dictionary will contain the separated list of strings.
    """

    parsed_details = self.get('changes/{}/revisions/{}/commit'
                              .format(change_id, revision))

    if 'message' not in parsed_details:
      raise RuntimeError('Message is not a field in returned json')

    result = dict(Closes=[], Resolves=[])
    for line in parsed_details['message'].splitlines():
      parts = line.split(':', 1)
      if len(parts) == 2:
        key, value = parts
        if key in ['Closes', 'Resolves']:
          issues = [item.strip() for item in value.strip().split(',')]
          result[key].extend(issues)
        elif key in ['Priority']:
          try:
            result[key] = int(value)
          except ValueError:
            pass
        else:
          result[key] = value.strip()

    return result

  def submit_change(self, change_id, author_id=None):
    request_url = 'changes/{}/submit'.format(change_id)

    try:
      if author_id is not None:
        return self.post(request_url, json={'on_behalf_of': author_id})
      else:
        return self.post(request_url)

    except requests.RequestException:
      logging.exception('Failed to set review score for change %s', change_id)
      return None

  def set_review(self, change_id, current_revision, review_dict):
    """
    Call out to the gerrit REST API to set a review comment on the specified
    change. The format for the review_dict is that of a ReviewInput json
    object.

    See: https://gerrit-review.googlesource.com/Documentation/
         rest-api-changes.html#review-input.
    """
    request_url = 'changes/{}/revisions/{}/review'.format(change_id,
                                                          current_revision)
    try:
      self.post(request_url, json=review_dict)
    except requests.RequestException:
      logging.exception('Failed to set review score for change %s', change_id)

  def get_username_from_email(self, email_address):
    """
    Call out to the gerrit REST API to get the user info associated with an
    email address, and return the gerrit username for that email address.
    """
    request_url = 'accounts/{}'.format(email_address)
    try:
      parsed_content = self.get(request_url)
      return parsed_content.get('username', None)
    except requests.RequestException:
      logging.exception('Failed to get username for email %s', email_address)
      return None
