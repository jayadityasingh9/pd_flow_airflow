#!/usr/bin/env bash
# One-screen status: is Airflow up on this host, and which PD tool jobs are live on the pd hosts.
#   bash status.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./env.sh

echo "== Airflow on $(hostname) (AIRFLOW_HOME=$AIRFLOW_HOME)"
[ -f "$AF_DB_DIR/HOST" ] && echo "   DB created on: $(cat "$AF_DB_DIR/HOST")  ($AF_DB_DIR)" || echo "   no metadata DB on this host yet"
N=0
for pid in $(pgrep -u "$(id -un)" -f airflow 2>/dev/null); do
  if { tr '\0' ' ' < "/proc/$pid/environ"; } 2>/dev/null | grep -q "AIRFLOW_HOME=$AIRFLOW_HOME"; then
    N=$((N+1)); echo "   pid $pid: $(tr '\0' ' ' < /proc/$pid/cmdline | cut -c1-100)"
  fi
done
[ "$N" -eq 0 ] && echo "   NOT running (start: tmux new -s pdflow 'bash $HERE/start.sh')"
if command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ":$AIRFLOW_PORT "; then
  echo "   listening on 127.0.0.1:$AIRFLOW_PORT"
fi

echo
echo "== PD tool jobs"
"$VPY" -m pdflow.cli list --live 2>/dev/null || python3 -m pdflow.cli list --live
