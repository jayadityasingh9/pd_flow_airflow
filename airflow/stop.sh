#!/usr/bin/env bash
# Stop a running `airflow standalone` for THIS AIRFLOW_HOME (never touches other Airflows).
# FC / build jobs on the pd hosts are detached (setsid over ssh) and keep running; after
# start.sh, "Clear" the task in the UI and it re-attaches to the running job.
#   bash stop.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./env.sh 2>/dev/null || true

PIDS=$(pgrep -u "$(id -un)" -f "airflow" 2>/dev/null | while read -r pid; do
  { tr '\0' ' ' < "/proc/$pid/environ"; } 2>/dev/null | grep -q "AIRFLOW_HOME=$AIRFLOW_HOME" && echo "$pid"
done)

if [ -z "${PIDS:-}" ]; then
  echo "No Airflow process found for AIRFLOW_HOME=$AIRFLOW_HOME on $(hostname)"
  exit 0
fi
# Tell the step operators' on_kill that this is a SHUTDOWN (leave FC jobs running for
# re-attach), not a user "Mark failed" (which does kill the remote job).
date > "$AIRFLOW_HOME/pdflow_shutdown"
echo "Stopping Airflow PIDs: $PIDS"
kill $PIDS 2>/dev/null || true
sleep 3
for pid in $PIDS; do kill -9 "$pid" 2>/dev/null || true; done
echo "Stopped."
