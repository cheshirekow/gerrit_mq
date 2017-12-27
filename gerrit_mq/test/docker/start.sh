#!/bin/bash
set -ue

# Start gerrit daemon
${GERRIT_HOME}/gerrit/bin/gerrit.sh start

# We need to run *something* to keep the docker container going since 
# gerrit runs in the background. We may as well run something useful so let's 
# pipe the error log to the console.
tail -f $GERRIT_HOME/gerrit/logs/error_log
