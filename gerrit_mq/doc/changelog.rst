=========
Changelog
=========

---------------
Changelog 0.3.0
---------------

common
======

* Webfront doesn't query gerrit every time the current queue is requested.
  Instead, the daemon queries gerrit at the polling period for the request list
  and caches the results in the local database. The webfront then returns
  records from this local cache when responding to user requests.
* Added support for "coalesced" merges. With coalesced merges, the daemon will
  collect a batch of changes and verify the resulting state of master after all
  changes in that batch are merged. This sacrifices a atomicity for performance
  and can accelerate the merge queue for projects with long build time.
* Added Documentation for the testing tools
* Expanded the testing tools and streamlined the testing setup. Most testing
  tools (except the daemon itself) now directly use the test credentials for
  ssh. It only takes a few seconds to setup the test environment.
* Added "Current Merge" to the webfront views. Every page displays the current
  merge (if there is one).
* Added live update (self refresh) to the webfront views so that the users dont
  have to manually refresh to see the updated state.

---------------
Changelog 0.2.0
---------------

webfront
========

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

daemon
======

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

common
======

* Database times are stored in UTC
* Gerrit account info is cached in local database reducing the number of hits
  to gerrit that are needed.
* Added sample systemd unit files for daemon and webfront
* Config file is now python, not hocon

tools
=====

* Added database migration tool
* Added tool to fetch missing AccountInfo from gerrit
* Added testing tool to generate a sequence of tiny non-conflicting changes and
  approve and queue them all.
* Added tool to generate executable python zipfile for easy distribution
* Added tool to gzip old uncompressed log directory

known issues
============

* Log stream error handlers are not implemented, if the log stream appears to
  freeze just refresh the page
* Fetching from gerrit often fails when the system is loaded during the build,
  don't be alarmed it if takes a minute or two for the merge queue to detect a
  canceled merge.
* History and detail pages don't monitor the status of the current merge, so
  they wont live-update when the merge finishes. You'll need to refresh to see
  if it has finished (or just pay attention to the log stream).

-----
0.2.1
-----

* Documentation update, switched to sphinx documentation
* Add cmake build system :(

-----
0.2.2
-----

* More documentation update, add README description and usage
* Add version number
