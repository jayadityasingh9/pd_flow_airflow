#!/usr/bin/env bash
# Start Airflow "standalone" (scheduler + dag-processor + triggerer + API/UI server) in the
# FOREGROUND. FC runs take hours, so start it inside tmux so it survives your logout:
#
#   tmux new -s pdflow 'bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/start.sh'
#
# Admin password (generated on first start): home/simple_auth_manager_passwords.json.generated
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./env.sh

[ -x "$AIRFLOW_BIN" ] || { echo "FATAL: Airflow not installed yet -- run: bash setup.sh" >&2; exit 1; }

# The metadata DB is node-local. A different host means a fresh (empty) DB: migrate it + pools.
if [ ! -f "$AF_DB_DIR/airflow.db" ]; then
  echo "-- no metadata DB on $(hostname) yet: creating it (run history starts empty on this host)"
  "$AIRFLOW_BIN" db migrate
  "$AIRFLOW_BIN" pools set pd_fc 2 "Concurrent Fusion Compiler steps" >/dev/null
  "$AIRFLOW_BIN" pools set pd_build 2 "Concurrent pyBuildSim RTL builds" >/dev/null
  hostname > "$AF_DB_DIR/HOST"
fi

rm -f "$AIRFLOW_HOME/pdflow_shutdown"     # written by stop.sh; see PdStepOperator.on_kill

# standalone spawns its children by invoking the bare command `airflow`: venv/bin must be on PATH.
export PATH="$VENV/bin:$PATH"

echo "Starting Airflow standalone on $(hostname):$AIRFLOW_PORT ..."
echo "  AIRFLOW_HOME = $AIRFLOW_HOME"
echo "  DAGs folder  = $AIRFLOW__CORE__DAGS_FOLDER"
echo "  metadata DB  = $AF_DB_DIR/airflow.db"
echo "  tunnel       : ssh -L ${AIRFLOW_PORT}:127.0.0.1:${AIRFLOW_PORT} ${USER}@$(hostname -f)"
echo "  UI           : http://127.0.0.1:${AIRFLOW_PORT}   (user admin; password in $AIRFLOW_HOME/simple_auth_manager_passwords.json.generated)"
echo "  Ctrl-C to stop. FC jobs already running on the pd hosts are NOT stopped by this."
exec "$AIRFLOW_BIN" standalone
