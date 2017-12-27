# Gerrit Merge Queue

A merge commit serializer for special branches.

# Test setup

There is a script to create a docker image with gerrit configured for two
users. Just execute

    python -m gerrit_mq.test.gerrit_docker build

to create the docker image and then

    python -m gerrit_mq.test.gerrit_docker start --debug

to start the contianer (`--debug` puts it in the foreground).

Once it's started open [https://localhost:8443] in a browser and use the
"Become" link to become one of the test users. Then add your public key
to that user, and clone the test repo with

    git clone ssh://test1@localhost:29418/mq_test

Get the commit hook with

  curl --insecure -Lo .git/hooks/commit-msg https://localhost:8443/tools/hooks/commit-msg
  chmod +x .git/hooks/commit-msg

Checkout a feature branch

  git checkout -b feature_001

Make a change

  cat > file_a.txt
  Hello world

  git add -A
  git commit
  git push -u origin
  git push origin HEAD:refs/for/master


# Notes on testing:

Gerrit 2.8.11 only offers `diff-hellman-group1-sha` as an exchange method, which
unfortunately OpenSSH (client) disables by default. To run tests againsts this
gerrit version in the docker container you'll need to add the following to
your `~/.ssh/config`:

    Host localhost
      KexAlgorithms +diffie-hellman-group1-sha1
      StrictHostKeyChecking no
      UserKnownHostsFile=/dev/null

Copy the commit message hook from the server using

    curl -Lo .git/hooks/commit-msg http://review.example.com/tools/hooks/commit-msg

This will append a random changeID to the change message.

Put the change out for review with

    git push origin HEAD:refs/for/master

# REST documentation

https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html

Test the rest API with (e.g. using the test instance credentials)

    curl -X GET --insecure --digest -u "merge_queue:IW/XXJ4G98nbrcSscjKJvIjHRgpoV7Ax4ptTtbQu2g" "https://localhost:8443/a/groups/3/members"


# Multi-daemon design

Webfront functions as a master / job dispatcher. It periodically queries gerrit
and determines what the current queue is for each project / branch that it
watchs, and copies the ChangeInfo objects into it's local database. It maintains
a priority queue of ChangInfo objects separatedy into buckets by project/branch.

Daemon acts as a slave / worker. It may have multiple configurations
where each configuration refers to a particular project, a selection of branches
to match, and a workspace to build each branch (multiple branches may be built
in the same workspace).

The Daemon queries the Webfront for new any jobs matchin it's filters. The
Webfront keeps track of any daemon currently merging a particular project/branch
and will hold off distributing any merges for a project/branch that already
has an actie merger.

# Optimistic merge design

When a daemon requests a new job the WebFront will hand out the next available
job in the queue for that project/branch. When the daemon finishes the
Webfront marks that job as successful. When the first merge in the p-queue is
marked successful the daemon will request gerrit merge for each item in the
queue up to the first item that is either un-built or failure. When a failure
occurs all subsequent jobs are canceled, the failing merge is evicted from the
queue and all daemons request new jobs.


# Changelog 0.2.0

## webfront

* Webfront serves json data, not rendered pages
* Views are rendered on client side with Moustache in javascript, instead of
  server side with Jinja in python
* More responsive log stream view
* Fix duplicate data in log-stream view
* Added daemon status page with pause/resume button
* Live-update of build duration for the active merge
* Prettier page navigation buttons
* History can be filtered by project or branch
* Log files are gzip compressed
* Log files are served directly from nginx
* History, queue, and details all link back to gerrit now

## daemon

* Active merge can be canceled by a link on the "details" page
* Merge will also be canceled if gerrit MQ+1 score is removed
* Daemon pause is active immediately following the current merge, as opposed
  to when the current queue is expired
* Daemon re-queries the queue after every merge, so any re-ordering on gerrit
  will be realized at the queue
* Queue sort order adds a "Priority" field which can be specified through
  commit message metadata "Priority: 0" will merge before "Priority: 1".
  The default is "Priority: 100".
* Daemon can now merge multiple projects.
* Added support for multiple queues configurable as a named
  (`project`, `branch_pattern`) and a set of build steps specific to that
  queue.
* Each queue get's it's own workspace preventing one queue from clobbering the
  build cache of another queue.
* Daemon will cycle through merges in it's queue no faster than the gerrit
  polling period.

## common

* Database times are stored in UTC
* Gerrit account info is cached in local database reducing the number of hits
  to gerrit that are needed.
* Added sample systemd unit files for daemon and webfront
* Config file is now python, not hocon

## tools

* Added database migration tool
* Added tool to fetch missing AccountInfo from gerrit
* Added testing tool to generate a sequence of tiny non-conflicting changes and
  approve and queue them all.
* Added tool to generate executable python zipfile for easy distribution
* Added tool to gzip old uncompressed log directory

## known issues

* Log stream error handlers are not implemented, if the log stream appears to
  freeze just refresh the page
* Fetching from gerrit often fails when the system is loaded during the build,
  don't be alarmed it if takes a minute or two for the merge queue to detect a
  canceled merge.
* History and detail pages don't monitor the status of the current merge, so
  they wont live-update when the merge finishes. You'll need to refresh to see
  if it has finished (or just pay attention to the log stream).
