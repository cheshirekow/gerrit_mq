"""Object Relational Model for merge-queue database."""

import enum
import json

import sqlalchemy
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey

GERRIT_TIME_SHORT_FMT = '%Y-%m-%d %H:%M:%S'

# A sqlalchemy concept, a kind of 'registry' of the SQL object mapping
Base = declarative_base()  # pylint: disable=invalid-name

# constants used to indicate build results


class StatusKey(enum.Enum):
  TIMEOUT = -3
  CANCELED = -2
  STEP_FAILED = -1
  SUCCESS = 0
  IN_PROGRESS = 1


class Cancellation(Base):   # pylint: disable=no-init
  """
  Whether or not a merge has been cancedled
  """

  __tablename__ = 'cancellations'
  __table_args__ = {'sqlite_autoincrement': True}

  # row/record id of merge to cancel
  rid = Column(Integer, primary_key=True)

  # who canceled
  who = Column(String)

  # when teh cancellation happened
  when = Column(DateTime)



class Builder(Base):  # pylint: disable=no-init
  """
  Information about servers available to build changes
  """
  __tablename__ = 'builders'
  __table_args__ = {'sqlite_autoincrement': True}

  # row/record id
  rid = Column(Integer, primary_key=True)

  # human readable / friendly name
  name = Column(String)

  # shared secret authentication key
  auth_key = Column(String)

  # Additional non-indexed information about this builder. In particular
  # contains the configuration for each project/branch build
  builder_meta = Column(String)

  def __repr__(self):
    return ('<Builder(id="{}", name="{}">'
            .format(self.rid, self.name))

  def as_dict(self):
    result = {key: getattr(self, key) for key
              in ['rid', 'name']}
    return result



class MergeStatusV0p1p0(Base):  # pylint: disable=no-init
  """
  Stores information about a merge attempt. This structure is serialized
  into a row of the SQLite database storing the merge history.
  """
  __tablename__ = 'merge_history_v0p1p0'
  __table_args__ = {'sqlite_autoincrement': True}

  id = Column(Integer, primary_key=True)  # pylint: disable=invalid-name
  gerrit_id = Column(String)
  change_id = Column(String)
  feature_branch = Column(String)
  target_branch = Column(String)
  owner = Column(String)
  request_time = Column(DateTime)
  start_time = Column(DateTime)
  end_time = Column(DateTime)
  result = Column(Integer)

  def __repr__(self):
    return ('<MergeResult(id="{}")>').format(self.id)


class MergeStatus(Base):  # pylint: disable=no-init
  """
  Data about an attempt to complete a merge.
  """
  __tablename__ = 'merge_history'
  __table_args__ = {'sqlite_autoincrement': True}

  # row/record id
  rid = Column(Integer, primary_key=True)

  # the name of the project
  project = Column(String, index=True)

  # the name of the target branch
  branch = Column(String, index=True)

  # rid of AccountInfo table
  owner_id = Column(Integer, ForeignKey('account_info.rid'))
  owner = relationship('AccountInfo')

  # gerrit change id
  change_id = Column(String)

  # time on the gerrit server of the first MQ+1 after the last MQ-1
  request_time = Column(DateTime)

  # time that the daemon actually started the merge
  start_time = Column(DateTime)

  # time that the daemon completed the merge
  end_time = Column(DateTime)

  # status of the merge. See values in the StatusKey enum.
  status = Column(Integer)

  # progress in fractions of 1/10,000. i.e. 10,000 means 100% done.
  progress = Column(Integer)

  # Additional non-indexed information as a json object. Includes things
  # like feature-branch, target-branch, etc.
  msg_meta = Column(String)

  def __repr__(self):
    return ('<MergeAttempt(id="{}", gerrit_id="{}/{}/{}">'
            .format(self.rid, self.project, self.branch, self.change_id))

  def as_dict(self):
    result = {key: getattr(self, key) for key
              in ['rid', 'project', 'branch', 'change_id', 'status',
                  'progress']}
    for key in ['request_time', 'start_time', 'end_time']:
      result[key] = getattr(self, key).strftime(GERRIT_TIME_SHORT_FMT)

    if self.msg_meta is None:
      result['metadata'] = {}
    else:
      result['metadata'] = json.loads(self.msg_meta)

    if hasattr(self, 'owner') and isinstance(self.owner, AccountInfo):
      result['owner'] = self.owner.as_dict()
    else:
      result['owner'] = {'rid' : self.owner_id,
                         'name' : '<unknown>',
                         'email' : '<unknown>',
                         'username' : '<unknown>'}

    return result


class AccountInfo(Base):  # pylint: disable=no-init
  """
  Local cache of gerrit AccoutnInfo objects  to reduce the number of gerrit
  REST hits that we have to make.
  """

  __tablename__ = 'account_info'

  # record id, matches _account_id from gerrit AccountInfo
  rid = Column(Integer, primary_key=True)

  # full name of the user
  name = Column(String)

  # email address of the user
  email = Column(String)

  # email address of the user
  username = Column(String)

  def __repr__(self):
    return ('<AccountInfo(id="{}", username="{}">'
            .format(self.rid, self.username))

  def as_dict(self):
    return {key: getattr(self, key) for key
            in ['rid', 'name', 'email', 'username']}


class ChangeInfo(Base):  # pylint: disable=no-init
  """
  Local cache of gerrit ChangeInfo objects to reduce the number of gerrit
  REST hits that we have to make.
  """
  __tablename__ = 'change_info'
  __table_args__ = {'sqlite_autoincrement': True}

  # row/record id
  rid = Column(Integer, primary_key=True)

  # unique identifier for when this change info was cached
  poll_id = Column(Integer, index=True)

  # time on the gerrit server of the first MQ+1 after the last MQ-1
  queue_time = Column(DateTime)

  # merge priority. 0 is highest priority, 100 is default priority. Lower
  # value has higher priority.
  priority = Column(Integer)

  # gerrit change id
  change_id = Column(String, index=True)

  # the name of the project
  project = Column(String, index=True)

  # the name of the target branch
  branch = Column(String, index=True)

  # header line of the commit message
  subject = Column(String)

  # current revision of the change
  current_revision = Column(String)

  # id of the owner AccountInfo (in account_info table)
  owner = Column(Integer, ForeignKey('account_info.rid'))

  # json-encoded dictionary of any metadata stored in colon-tags in the
  # commit message.
  message_meta = Column(String)

  def __repr__(self):
    return ('<ChangeInfo(change_id="{}/{}/{}")>'
            .format(self.project, self.branch, self.change_id))

  def as_dict(self):
    result = {key: getattr(self, key) for key
              in ['rid', 'change_id', 'current_revision', 'priority',
                  'project', 'branch', 'owner', 'subject']}
    result['queue_time'] = self.queue_time.strftime(GERRIT_TIME_SHORT_FMT)
    result['message_meta'] = json.loads(self.message_meta)
    return result


class QueueSpec(Base):  # pylint: disable=no-init
  """
  Specification/Configuration for a serialization queue
  """

  __tablename__ = 'queue_spec'
  __table_args__ = {'sqlite_autoincrement': True}

  # row/record id
  rid = Column(Integer, primary_key=True)

  # The project this queue applies to
  project = Column(String, index=True)

  # Name of this queue (same as branch if this is not a regex)
  name = Column(String)

  # Regular expression for branch names to match
  branch = Column(String)

  # JSON encoded dictionary to use as the environment
  build_env = Column(String)

  # Whether or not the host environment should be merged into the build
  # environment
  merge_build_env = Column(Boolean)

  # Json endcoded list of commands to execute for the build
  build_steps = Column(String)

  # Submit with rest api. If false, the last step in `build_steps` should
  # do the submit
  submit_with_rest = Column(Boolean)

  def __repr__(self):
    return ('<QueueSpec({}, {})>'
            .format(self.project, self.name))

  def as_dict(self):
    result = {key: getattr(self, key) for key
              in ['rid', 'project', 'name', 'branch', 'merge_build_env',
                  'submit_with_rest']}
    result['build_env'] = json.loads(self.build_env)
    result['build_steps'] = json.loads(self.build_steps)
    return result


def init_sql(database_url):
  """
  Initialize sqlalchemy and the sqlite database. Returns a session factory.
  """
  engine = sqlalchemy.create_engine(database_url, echo=False)
  Base.metadata.create_all(engine)
  return sqlalchemy.orm.sessionmaker(bind=engine)
