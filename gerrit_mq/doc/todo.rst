====
TODO
====

--------
webfront
--------

* detect if running from codezip or source files, add a watch to all source
  files, templates, codezip, or config and restart if any of them change.

------------
details page
------------

* Implement failure handlers for ajax so that stream doesn't stop on error
* If status is "IN_PROGRESS" then continue to poll status (until it's not
  IN_PROGRESS)
* Don't latch on fetch_full_log since sometimes the server doesn't allow us
  to request byte range even if it's not gzipped yet (this seems to happen
  when the file is very small). Instead, just go back to fetching the HEAD
  until the size changes.
* Cannot read property "split" of null
* Add anchors to each <pre> that we plop onto the page, allowing people to
  link directly to a particular section.
* Keep polling the status and refresh the status table if it finishes.
* Keep polling the log URI with a HEAD request even if range requests are not
  supported. If the Content-Length changes then we know that the data has
  changed and we should fetch the whole log.

------------
history page
------------

* Add live filtering by project/branch
* Add a view for the current merge if one is active, and a button to cancel it
* Keep polling history [0] and refresh if it finishes, or if a new one starts.

----------
queue page
----------

* Add live filtering by project/branch
* Lint common.py and daemon.py and add to buildsystem

-----------------
other / all pages
-----------------

* Configure gerrit URL correctly
* If daemon is offline or paused show a banner on every page

------
daemon
------

* Implement optimistic merge
* Implement standard gerrit workflow:

  * instead of checking out the feature branch, just checkout the change
    commit and the target branch. Reverse the order of the merge. Merge the
    change into the target branch, instead of vice-versa.

* detect if running from codezip or source files, add a watch to all source
  files, templates, codezip, or config and restart if any of them change.
* Implement a merge timeout

-----
tools
-----

* Add tool to render templates out of pyzip using the gerrit URL and pages
  directory from configuration.
* Add tool to generate nginx configuration file using template stored in
  zipfile and details from the config.

-----------
other / all
-----------

* Don't have webfront query gerrit every time for the request list. Instead,
  let the daemon query gerrit for the request list and cache the results in
  the database. Then let the webfront query from the database when responding
  to user requests.
* Add a buildsystem of somesort.


