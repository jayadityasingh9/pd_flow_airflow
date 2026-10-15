#!/usr/bin/env bash
# Start Airflow "standalone" (scheduler + dag-processor + triggerer + API/UI server) in the
# FOREGROUND. FC runs take hours, so start it inside tmux so it survives your logout:
#
#   tmux new -s pdflow 'bash <checkout>/airflow/start.sh'
#
# Admin password (generated on first start): home/simple_auth_manager_passwords.json.generated
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./env.sh

[ -x "$AIRFLOW_BIN" ] || { echo "FATAL: Airflow not installed yet -- run: bash setup.sh" >&2; exit 1; }

# The metadata DB is node-local. A different host means a fresh (empty) DB: migrate it.
if [ ! -f "$AF_DB_DIR/airflow.db" ]; then
  echo "-- no metadata DB on $(hostname) yet: creating it (run history starts empty on this host)"
  "$AIRFLOW_BIN" db migrate
  hostname > "$AF_DB_DIR/HOST"
fi
# The pools the DAG's tasks are bound to must exist in THIS DB, or those tasks are never scheduled
# ("non-existent pool"). `pools set` is idempotent, so do it on every start (covers a DB that was created
# some other way, e.g. by an `af.sh` command or an older setup.sh, and never got the pools).
"$AIRFLOW_BIN" pools set pd_fc 2 "Concurrent Fusion Compiler steps (each ~16 cores + 1 FC license)" >/dev/null 2>&1 \
  && "$AIRFLOW_BIN" pools set pd_build 2 "Concurrent pyBuildSim RTL builds" >/dev/null 2>&1 \
  && echo "-- pools pd_fc=2, pd_build=2 ensured" || echo "-- WARNING: could not set pools (will retry once Airflow is up)"

mkdir -p "$AIRFLOW_HOME"
rm -f "$AIRFLOW_HOME/pdflow_shutdown"     # written by stop.sh; see PdStepOperator.on_kill

# The admin password is generated on first start into a file that is only protected by the umask (0644):
# anyone logged into this host could then use the UI as admin = run jobs as you. Lock it to 0600.
PW_FILE="$AIRFLOW_HOME/simple_auth_manager_passwords.json.generated"
[ -f "$PW_FILE" ] && chmod 600 "$PW_FILE"
( for i in $(seq 1 60); do
    sleep 10
    if [ -f "$PW_FILE" ]; then chmod 600 "$PW_FILE"; break; fi
  done ) &

# Unpause raven_pd_flow once the DAG processor has registered it (a paused DAG queues runs forever),
# and make sure the pools exist (second chance, in case the DB was still locked above).
( for i in $(seq 1 30); do
    sleep 20
    if "$AIRFLOW_BIN" dags unpause raven_pd_flow >/dev/null 2>&1; then
      echo "start.sh: raven_pd_flow is unpaused"
      "$AIRFLOW_BIN" pools set pd_fc 2 "Concurrent Fusion Compiler steps (each ~16 cores + 1 FC license)" >/dev/null 2>&1
      "$AIRFLOW_BIN" pools set pd_build 2 "Concurrent pyBuildSim RTL builds" >/dev/null 2>&1
      break
    fi
  done ) &

# standalone spawns its children by invoking the bare command `airflow`: venv/bin must be on PATH.
export PATH="$VENV/bin:$PATH"

echo "Starting Airflow standalone on $(hostname):$AIRFLOW_PORT ..."
echo "  instance     = $AIRFLOW_HOME   (airflow.cfg, task logs, admin password; PDFLOW_INSTANCE)"
echo "  code         = $PDFLOW_ROOT"
echo "  DAGs folder  = $AIRFLOW__CORE__DAGS_FOLDER"
echo "  metadata DB  = $AF_DB_DIR/airflow.db"
echo "  tunnel       : ssh -L ${AIRFLOW_PORT}:127.0.0.1:${AIRFLOW_PORT} ${USER}@$(hostname -f)"
echo "  UI           : http://127.0.0.1:${AIRFLOW_PORT}   (user admin; password in $AIRFLOW_HOME/simple_auth_manager_passwords.json.generated)"
echo "  Ctrl-C to stop. FC jobs already running on the pd hosts are NOT stopped by this."
exec "$AIRFLOW_BIN" standalone
