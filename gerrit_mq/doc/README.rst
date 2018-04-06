==================
Gerrit Merge Queue
==================

A merge commit serializer for special branches.

-------
Purpose
-------

``gerrit-mq`` solves a particular problem in continuous integeration
whereby two separate changes are syntactically conflict free but semantically in
conflict.

Here's a motivating (though contrived) example. Let's say that our code
repository has the following:

``foo.py``:

.. code::

    def bar(x):
      return x + 10

``foo_test.py``:

.. code::

    import unittest

    import foo

    class TestFoo(unittest.TestCase):
      def test_foo(self):
        self.assertGreater(foo.bar(0), 5)

    if __name__ == '__main__':
      unittest.main()


Now let's say that Alice decies to improve this loose test with some more
meaningful bounds and makes the following change to ``foo_test.py``:

.. code::

    class TestFoo(unittest.TestCase):
        def test_foo(self):
          self.assertEqual(foo.bar(0), 10)

And let's say that Bob makes the following change to ``foo.py``:

.. code::

    def bar(x):
      return x + 6

If we have designed our continuous integration infrastructure with pre-submit
build-and-test (on, say, jenkins or buildbot), then both of these changes will
pass pre-submit no problem. However, the two changes are logically incompatible.
If both changes are merged the unit test will begin to fail. One way to deal
with this issue is after-the-fact. We can add a post-merge job to our continuous
integration server to give us a heads-up every time something like this happens.
However, as your team scales, this becomes impractical for two reasons:

1. As the submit rate goes up, the frequency of this occurance will go up
2. Once a breaking change is in, then all pre-submit jobs will fail. Your
   CI pre-submit job becomes an ignorable signal (if advisory) or the entire
   pipeline is frozen until someone can fix the build (if manditory).

``gerrit-mq`` attempts to solve this problem by re-executing the
build-and-tests checks on each merge in serial order. It ensures that no
conflicts like that illustrated above ever get merged. In the above scenario,
whichever merge was queued first will pass, and the second will fail.

------------
How it works
------------

There are two components to the merge queue:

1. The merge daemon: polls gerrit for new merge requests and verifies/merges
   them
2. The web frontend: displays the current queue, past job summary table, and
   job output streams

When ``gerrit-mq`` polls gerrit it looks for new "merge requests". A merge
request is any change which is ``Code-Review: +2`` and ``Merge-Request: +1``.
The ``Merge-Request`` label is not a gerrit built-in and so must be added. For
example you might add the following to ``all_projects.config``::

    [access "refs/heads/*"]
    ...
      label-Merge-Queue = -1..+1 group TestGroup

Because the queue is maintained in gerrit, when a merge fails ``gerrit-mq`` will
add a ``Merge-Queue: -1`` label. In order to allow re-request of a merge,
``gerrit-mq`` resolves the ``Merge-Queue`` label score as "The highest score
after the latest -1".


-----
Usage
-----

.. code-block:: text

    usage: gerrit-mq [-h] [-c CONFIG_PATH] [-l {debug,info,warning,error}] CMD ...

    Entry point / launcher for gerrit-mq components.

    positional arguments:
      CMD
        webfront            Start the merge-queue master service.
        get-next            Retrieve the next merge request.
        get-queue           Retrieve the currently cached queue in json format
        daemon              Execute the daemon process.
        render-templates    Render jinja2 templates into full html files.
        migrate-database    Migrate a database from one schema to another
        sync-account-table  Fetch account table from gerrit and store locally
        gzip-old-logs       Gzip files in an old log directory
        poll-gerrit         Hit gerrit REST and read off the current queue of
                            merge requests. Write that to a json file.

    optional arguments:
      -h, --help            show this help message and exit
      -c CONFIG_PATH, --config-path CONFIG_PATH
                            path to config file
      -l {debug,info,warning,error}, --log-level {debug,info,warning,error}

-------------
Configuration
-------------

``gerrit-mq`` takes a configuration file as input. The configuration file is
python and will be ``exec()``. See the example configuration in
``samples/mqconfig.py`` which contains comments describing what each option
means.

``gerrit-mq`` supports multiple logical "queues". Each queue is defined by:

1. which gerrit project the queue applies to
2. a pattern used to match against branch names
3. a unique name for the queue
4. a dictionary describing the environment of subprocess calls
5. a list of commands to execute to verify the merge request, if any exits with
   non-zero exit code then verification fails
6. a flag indicating whether or not to finally merge using the gerrit rest API
   (you set this to false if the last command in your list of commands does
   the actual merge)

This allows you to configure different verification steps for different
projects or different branches. It also allows you to specify a common queue
for a pattern of branches. For instance,
``release-candidate/\d{4}-\d{2}-\d{2}`` will match branches like

* ``release-candidate/2018-01-14``
* ``release-candidate/2018-02-12``

All jobs from a single queue are built/verified in the same git working tree.
This means that (unless you otherwise specify) the merge queue will generally
execute an incremental build. You can, of course, remove the build tree as your
first step to get a clean build every time.

---------
Execution
---------

Start the daemon with::

    gerrit-mq --config config.py daemon

Start the webfront with ::

    gerrit-mq --config config.py webfront

The webfront only serves ``JSON``. Use::

    gerrit-mq render-templates <outdir>

to create the html document root for the webfront views.

The directory ``samples/`` contains an example nginx configuration and
``systemd`` unit files for the webfront and daemon. These all presume that
the system has a user ``mergequeue``, the config file is at
``/home/mergequeue/config.py`` and the html document root is at
``/home/mergequeue/pages``.


-----------
Init System
-----------

If you'd like to run ``gerrit-mq`` on startup in ubuntu, there are sample
``systemd`` unit files in the ``samples/`` directory.

----------
Test setup
----------

There is a script to create a docker image with gerrit configured for two
users. Just execute::

    python -Bm gerrit_mq.test.gerrit_docker build

to create the docker image and then::

    python -Bm gerrit_mq.test.gerrit_docker start --debug

to start the container (``--debug`` puts it in the foreground).

Once it's started open http://localhost:8081 in a browser and use the
"Become" link to become one of the test users. Then add your public key
to that user.

Start the webfront and the nginx forward proxy::

    python -Bm gerrit_mq --config gerrit_mq/test/mqconfig.py webfront
    python -Bm gerrit_mq.test --config gerrit_mq/test/mqconfig.py start-nginx

And check it out at http://localhost:8080.

Now start the daemon with::

    python -Bm gerrit_mq --config gerrit_mq/test/mqconfig.py daemon

Add your public key to the mergequeue user on gerrit
TODO(josh): plumb --identity through the daemon config and use the testing key

You can submit multiple jobs for testing with::

    python -Bm gerrit_mq.test --config gerrit_mq/test/mqconfig.py \
        create-reviews --approve --queue 5

You can manually clone the test repo with::

    git clone ssh://test1@localhost:29418/mq_test

Get the commit hook with::

    curl --insecure -Lo .git/hooks/commit-msg http://localhost:8081/tools/hooks/commit-msg
    chmod +x .git/hooks/commit-msg

Checkout a feature branch::

    git checkout -b feature_001

Make a change::

    cat > file_a.txt
    Hello world

    git add -A
    git commit
    git push -u origin
    git push origin HEAD:refs/for/master


----------------
Notes on testing
----------------

Gerrit 2.8.11 only offers ``diff-hellman-group1-sha`` as an exchange method,
which unfortunately OpenSSH (client) disables by default. To run tests againsts
this gerrit version in the docker container you'll need to add the following to
your ``~/.ssh/config`` ::

    Host localhost
      KexAlgorithms +diffie-hellman-group1-sha1
      StrictHostKeyChecking no
      UserKnownHostsFile=/dev/null

Copy the commit message hook from the server using::

    curl -Lo .git/hooks/commit-msg http://review.example.com/tools/hooks/commit-msg

This will append a random changeID to the change message.

Put the change out for review with::

    git push origin HEAD:refs/for/master

Create test commits for coalesced merge::

    python -m gerrit_mq.test -c test/config.py create-reviews --approve --queue --repo-path /tmp/mq_test --branch build pass-fail P P P P F P P P P
