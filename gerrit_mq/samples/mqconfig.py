import os

# root directory for data
DATA_ROOT = '/home/mergequeue'
USER = os.getenv('USER', 'mergequeue')
HOME = os.getenv('HOME', '/home/{}'.format(USER))

# We'll reuse this build environment for multiple queues
COMMON_BUILDENV = {
    'PATH': ['/usr/local/bin', '/usr/bin', '/sbin', '/bin',],
    'DEBIAN_FRONTEND' : 'noninteractive',
    'HOME' : HOME,
    'LANG' : 'en_US.UTF-8',
    'LANGUAGE' : 'en_US',
    'LC_ALL' : 'C',
    'PYTHONDONTWRITEBYTECODE' : '1',
}

# This is the set of steps that we do for most queues
COMMON_BUILDSTEPS = [
    ['make', 'ci-clean'],
    ['make', 'ci-build'],
]

MOVE_FLASHPACK_PATH = (
  './shared/tools/build_tools/executables/move_flashpack_to_directory.py')


# This is the sqlalchemy URL of the sqlite database to use
db_url = 'sqlite:///' + os.path.join(DATA_ROOT, 'mergedb.sqlite')

# This is the directory where we'll store our log files. This includes logs for
# ``gerrit-mq`` as well as logs for the merge attempts
log_path = os.path.join(DATA_ROOT, 'logs')

# This dictionary contains authentication information for the gerrit instance
gerrit = {

    # HTTP auth credentials  and options for accessing the gerrit REST API
    'rest': {
        # Set to true for testing with a gerrit instance over https with a
        # self-signed cert. DO NOT SET FALSE IN PRODUCTION!!!
        'disable_ssl_certificate_validation': False,

        # The base URL for your gerrit instance
        'url': 'https://gerrit.example.com',

        # Gerrit username used to login to gerrit. You should add a gerrit user
        # specifically for the mergequeue and it should have the ability to
        # query all changes and to merge changes.
        'username': 'mergequeue',

        # The HTTP password for this user. It can be set in the webUI if your
        # `mergequeue` user has login access. Otherwise use
        # ssh -p 29418 gerrit.example.com \
        #   'gerrit set-account --add-ssh-key "ssh-rsa <pubkey> <usercomment>" mergequeue'
        # Note the use of double quotes b/c bash will chomp the first set when
        # it passes the argument to ssh.
        'password': 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqurstuvwxyz',
    },

    # SSH access parameters. There's no option (yet) to override SSH identity
    # so you'll need the identity file in a place that the ssh client will
    # look for it.
    'ssh': {
        # The gerrit username
        'username' : 'mergequeue',

        # The hostname of the gerrit server
        'host': 'gerrit.example.com',

        # what port is open for ssh access. Default is 29418
        'port': 29418,

        # Whether or not to verify the host key.
        # DO NOT SET FALSE IN PRODUCTION!!!
        'check_hostkey': True,
    },
}

# Configuration options for the web frontend.
webfront = {
    # True to turn on flask debugging. DO NOT SET TRUE IN PRODUCTION!!
    'flask_debug': False,

    # Poll gerrit every this many seconds
    'poll_period': 60,

    # What interface/port to listen on for connections. You probably want to
    # reverse-proxy this through nginx/apache to secure it with https.
    'listen' : {
        'host' : '127.0.0.1',
        'port' : 8081,
    },

    # The public base URL used to access this page. This is used to fill in
    # some links on templates as well as to provide gerrit comments that link
    # to the merge status pages.
    'url': 'https://mergequeue.example.com',

    # This secret key is used to encrypt flask sessions
    'secret_key' : 'abcdABCDefghEFGHijklIJKL',
}

# Here we specify our different 'queues'. Each logical queue can have a
# different set of build steps, or a different build environment. All
# merges built in a single queue will be done in the same directory so
# you can separate incremental build flows by using separate queues.
queues = [{
    # Example of maybe our main branch merge job. Perhaps it's different from
    # other jobs because we upload some build artifacts to an artifact
    # repository
    'project' : 'mainproject',
    'branch' : 'master',
    'build_env': COMMON_BUILDENV,
    'merge_build_env': False,
    'build_steps': [
        ['make', 'ci-clean'],
        ['make', 'ci-build'],
        ['make', 'ci-upload'],
    ],
    'submit_with_rest': True,
}, {
    # Example of maybe our release candidate. Maybe these branches are longer
    # lived and have fewer commits so we build them in a separate directory.
    # This prevents their build from thrashing the build-cache of the master
    # build.
    'project' : 'mainproject',
    'branch' : r'^rc/\d{4}\-\d{2}\-\d{2}$',
    'name' : 'release',
    'build_env': COMMON_BUILDENV,
    'merge_build_env': False,
    'build_steps': COMMON_BUILDSTEPS,
    'submit_with_rest': True,
}, {
    # Maybe we also have a manufacturing branch which is similarly long-lived
    # but generally somewhat divergent from the other two.
    'project' : 'mainproject',
    'branch' : r'^mfg/\d{4}\-\d{2}\-\d{2}$',
    'name' : 'manufacturing',
    'build_env': COMMON_BUILDENV,
    'merge_build_env': False,
    'build_steps': COMMON_BUILDSTEPS,
    'submit_with_rest': True,
}]

# This is the configuration for the daemon process
daemon = {
    # This is the list of queues that the daemon should enable. The reason this
    # is separate from the definition of queues above is so that one
    # specification of queues can be shared across multiple machines. In that
    # case you would enable different queues for different daemons.
    'queues' : [
        ('mainproject', 'master'),
        ('mainproject', 'manufacturing'),
        ('mainproject', 'release'),
        ('mainproject', 'app-release')
    ],

    # When we checkout the source for each project, we will do so in a project
    # directory as a subdirectory of this location.
    'workspace_path' : DATA_ROOT,

    # Instead of verifying each change one by one, coalesce up to this many
    # changes together and verify the entire batch. For example with
    # `coalesce_count=5` the daemon will checkout the base branch as a temporary
    # verification branch. Then it will merge the next 5 queued changes into
    # that branch one-by-one. Finally it will run the verification steps. If
    # the verification passes, it will trigger gerrit merge for each of those
    # 5 changes. Note that this somewhat breaks atomicity of the verification
    # process but can significantly increase the merge rate.
    'coalesce_count' : 5,

    # The offline sentinel is a file which, if it exists, will cause the
    # daemon to stop verifying and merging changes. It effectively pauses
    # the queue so one can safely perform maintenance / shutdown the machine
    'offline_sentinel_path' : os.path.join(DATA_ROOT, 'pause'),

    # The daemon will poll for new changes on gerrit every this many seconds
    'poll_period' : 60,

    # The daemon will configure the given directory as a ccache directory of
    # the given size, and export ccache environment variables. This allows a
    # single ccache directory to be shared across queues.
    'ccache' : {
        'path' : os.path.join(DATA_ROOT, '.ccache'),
        'size' : '100G',
    },

    # The daemon will write it's pid to this file. It is used to prevent
    # multiple startup as well as to help debug.
    'pidfile_path' : os.path.join(DATA_ROOT, 'pid'),

    # Unless this is true, gerrit-mq will post comments to gerrit changes to
    # indicate status. For instance it will post a comment when merge
    # verification starts. It will post another comment when verification ends
    # with the result of the verification and a link to the job outputs.
    'silent' : False,
}
