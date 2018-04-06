------------------
REST documentation
------------------

https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html

Test the rest API with (e.g. using the test instance credentials)::

    curl -X GET --insecure --digest -u "merge_queue:<http-password>" "https://localhost:8443/a/groups/3/members"


-------------------
Multi-daemon design
-------------------

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

-----------------------
Optimistic merge design
-----------------------

When a daemon requests a new job the WebFront will hand out the next available
job in the queue for that project/branch. When the daemon finishes the
Webfront marks that job as successful. When the first merge in the p-queue is
marked successful the daemon will request gerrit merge for each item in the
queue up to the first item that is either un-built or failure. When a failure
occurs all subsequent jobs are canceled, the failing merge is evicted from the
queue and all daemons request new jobs.
