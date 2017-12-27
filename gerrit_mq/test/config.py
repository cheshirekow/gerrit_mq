import os

# root directory for data
DATA_ROOT = os.path.join(os.getenv('HOME', '/tmp'), 'merge_queue')

CONFIG = {
    # shared config
    # ------------------------------------------------------
    # location of the sqlite database where the merge daemon logs its merges
    'db_url': 'sqlite:///' + os.path.join(DATA_ROOT, 'mergedb.sqlite'),

    # this is where we will store a log from each merge attempt, the log
    # contains stdout and stderr from each build step
    'log_path': os.path.join(DATA_ROOT, 'logs'),

    'gerrit': {
        'rest': {
            # when testing with a local gerrit instance it will provide a
            # self-signed certificate so we must disable certificate checks
            # for our REST calls
            'disable_ssl_certificate_validation': True,

            # base URL for gerrit
            'url': 'https://localhost:8443',

            # HTTP user name
            'username': 'merge_queue',

            # HTTP password
            'password': 'IW/XXJ4G98nbrcSscjKJvIjHRgpoV7Ax4ptTtbQu2g',
        },

        'ssh': {
            # username to use for git clone
            'username' : 'merge_queue',

            # hostname of the machine
            'host': 'localhost',

            # the port gerrit is listening on for ssh connections
            'port': 29418,

            # whether or not to verify host key (should be yes for deployed,
            # because we grab the host key for the docker container)
            'check_hostkey': False,
        },
    },

    # options for webfront
    #-------------------------------------------
    'webfront': {
        # Whether or not to enable flask debug interface
        'flask_debug': True,

        # Poll gerrit every this often for new change status
        'poll_period': 10,

        # Listen on this address and port
        'listen' : {
            'host' : '127.0.0.1',
            'port' : 8081,
        },

        # Public-facing root url of the web frontend. This should include any
        # network translation, server redirect, or containerization. This is
        # only used to render links for navigation of the web frontend.
        'url': 'http://localhost:8080',

        # Secret key for debug service
        'secret_key' : 'TaiHenotguj9osOckphatAtNurEam,',
    },


    # configuration option for each project/branch queue
    # ------------------------------------------------------
    # TODO(josh): move to database
    'queues': [{
        # project this queue applies to
        'project' : 'mq_test',

        # regex pattern for branches this queue applies to
        'branch' : 'master',

        # [optional]: name for the queue. The queue name is the same as the
        # branch name if it is not specified, though the branch must not be
        # a pattern.
        # 'name' : 'master',

        # environment used for subprocess steps
        'build_env': {
            'PATH': [
                '/usr/bin',
                '/bin',
            ]
        },

        # if true, build_env is merged with the environment of the daemon
        'merge_build_env': False,

        # build steps we perform to verify the build before merging
        'build_steps': [['/bin/sleep', '10'],
                        ['/bin/true']],

        # Use gerrit rest interface to submit the change after verification.
        # Set to false if using an external tool, in which case the tool should
        # be the last item in build_steps.
        'submit_with_rest': True,
    }, {
        'project' : 'mq_test',
        'branch' : 'rc*',
        'name' : 'release-candidate',
        'build_env': {
            'PATH': [
                '/usr/bin',
                '/bin',
            ]
        },
        'merge_build_env': False,
        'build_steps': [['/bin/true']],
        'submit_with_rest': True,
    }, {
        'project' : 'mq_test',
        'branch' : 'build',
        'build_env': {
            'PATH': [
                '/usr/bin',
                '/bin',
            ]
        },
        'merge_build_env': False,
        'build_steps': [['python', './build.py']],
        'submit_with_rest': True,
    }],



    # configuration for the daemon
    # ------------------------------------------------------
    # NOTE(josh): to be removed when master/slave framework is complete
    'daemon' : {
        # list of queues this builder should build
        'queues' : [
            ('mq_test', 'master'),
            ('mq_test', 'release-candidate'),
        ],

        # root of the workspace
        'workspace_path' : os.path.join(DATA_ROOT, 'workspace'),

        # number of changes to coalesce in optimistic merge strategy
        # (zero means serial merges)
        'coalesce_count' : 5,

        # Location of a file for which existence pauses the merge queue
        'offline_sentinel_path' : os.path.join(DATA_ROOT, 'pause'),

        # How long to wait (in seconds) between polling gerrit when there is
        # nothing in the queue (othewise will poll after each merge attempt).
        'poll_period' : 20,

        'ccache' : {
            # CCACHE_DIR environment variable
            'path' : os.path.join(DATA_ROOT, 'ccache'),

            # How bit to make the ccache
            'size' : '100G',
        },

        # Write the PID of the daemon process here
        'pidfile_path' : os.path.join(DATA_ROOT, 'pid'),

        # If true, don't post updates to gerrit. If this is a test daemon then
        # you should only operate on queues with submit_with_rest=False and
        # steps[-1] should not submit the change.
        'silent' : False,
    },



    # NOTE(josh): stuff of the future
    # configuration for each builder
    # ------------------------------------------------------
    # TODO(josh): move to database
    'builders' : [{
        # human readable name
        'name' : 'test',

        # shared secret between master/slave
        'auth_key' : 'UziMYI1Rr03ke1r9/YAnBvrdddE=',

        # list of queues this builder should build
        'queues' : [
            ('mq_test', 'master'),
            ('mq_test', 'release-candidate'),
        ],

        # root of the workspace
        'workspace_path' : '/home/merge_queue/workspace'
    }, {
        'name' : 'mq1',
        'auth_key' : 'UziMYI1Rr03ke1r9/YAnBvrdddE=',
        'queues' : [
            ('aircam', 'master'),
        ],
        'workspace_path' : '/home/merge_queue/workspace'
    }, {
        'name' : 'mq2',
        'auth_key' : 'uRFW4QSS14Ub7cnL9O46Ru3mKxY=',
        'queues' : [
            ('aircam', 'rc*'),
            ('aircam', 'release'),
        ],
        'workspace_path' : '/home/merge_queue/workspace'
    }]
}
