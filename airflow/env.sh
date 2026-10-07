#!/usr/bin/env bash
# Shared environment for the Raven PD-flow Airflow install.  Source this from the other scripts:
#   source env.sh
#
# Everything is isolated under the project directory:
#   airflow/venv/   the Airflow install (isolated Python venv, built by setup.sh)
#   airflow/home/   AIRFLOW_HOME: airflow.cfg, logs, generated admin password
#   airflow/dags/   the DAG(s)
#   pdflow/         the flow logic (plain Python, imported by the DAG; on PYTHONPATH below)
#   config/fixed.yaml  every predefined (non per-run) value
#
# Run directories are NOT here -- they live under runs_root in config/fixed.yaml
# (/home/jsingh9.scratch/raven_local_ws/<run_tag>/).

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Nothing this Airflow creates (run dirs, generated scripts executed on the pd hosts) is group-writable.
umask 022
export PDFLOW_ROOT="$(cd "$HERE/.." && pwd)"

# Per-checkout overrides (git-ignored), e.g. AIRFLOW_PORT, AF_DB_DIR, VENV -- see local.env.example
[ -f "$HERE/local.env" ] && source "$HERE/local.env"

# --- Interpreter Airflow itself runs on ---------------------------------------------------
# System python3.11: its sqlite3 supports the deterministic functions Airflow's metadata DB
# needs (the freeware pythons do not). Verified on mipscs82/mipscs85/mipspd81.
export AF_BASE_PY="${AF_BASE_PY:-/usr/bin/python3.11}"
export AIRFLOW_VERSION="3.3.2"
export PYMAJMIN="3.11"

export VENV="${VENV:-$HERE/venv}"
export VPY="$VENV/bin/python"
export AIRFLOW_BIN="$VENV/bin/airflow"

export AIRFLOW_HOME="$HERE/home"
export AIRFLOW__CORE__DAGS_FOLDER="$HERE/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES="False"
# A fresh DB would create the DAG PAUSED: triggered runs then sit "queued" forever.
export AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION="False"
export AIRFLOW__CORE__PARALLELISM="8"
# DAG parsing on a loaded host: normal parse is ~1.5 s, but frozen login servers took 50 s and hit
# the 50 s processor timeout -> DAG marked stale -> no Trigger button. Give it room, and do not
# mark the DAG stale after one slow/killed parse.
export AIRFLOW__CORE__DAGBAG_IMPORT_TIMEOUT="240"
export AIRFLOW__DAG_PROCESSOR__DAG_FILE_PROCESSOR_TIMEOUT="300"
export AIRFLOW__DAG_PROCESSOR__STALE_DAG_THRESHOLD="1800"
export AIRFLOW__DAG_PROCESSOR__MIN_FILE_PROCESS_INTERVAL="60"
# Long FC steps: be generous before declaring a task's heartbeat lost.
# 20 min: survives the ~10 min host freezes seen, yet a task orphaned by an Airflow restart is
# noticed (and retried -> re-attaches to its still-running FC job) within 20 min.
export AIRFLOW__SCHEDULER__TASK_INSTANCE_HEARTBEAT_TIMEOUT="1200"
# The login servers (mipscs8x) are shared and often heavily loaded (load > 100 seen). With the
# defaults (3 failed heartbeats -> task SIGTERMed; 30 s health thresholds) a slow host makes
# Airflow kill/mark-dead its own healthy tasks -- which is how the mipscs82 instance degraded.
# 2026-10-06: mipscs82 froze ~10 min (heartbeat calls took ~700 s). The task's execution-API token
# (default 10 min) expired during the freeze, every later heartbeat was rejected, the supervisor
# gave up after 20 and killed the task. So: tokens valid 24 h, ~1 h of failed heartbeats tolerated.
export AIRFLOW__WORKERS__MAX_FAILED_HEARTBEATS="200"
export AIRFLOW__EXECUTION_API__JWT_EXPIRATION_TIME="86400"
export AIRFLOW__SCHEDULER__SCHEDULER_HEALTH_CHECK_THRESHOLD="180"
export AIRFLOW__TRIGGERER__TRIGGERER_HEALTH_CHECK_THRESHOLD="180"

# --- Metadata DB on node-LOCAL disk, NOT on scratch --------------------------------------
# SQLite over NFS + Airflow's concurrent processes corrupts the DB (seen in freq_hist_jenkins:
# "database disk image is malformed", scheduler died). So the DB is tied to the host where
# start.sh runs; run.json in each run dir lets a run resume from any host.
export AF_DB_DIR="${AF_DB_DIR:-/var/tmp/${USER:-jsingh9}_pdflow_airflow_db}"
mkdir -p "$AF_DB_DIR" 2>/dev/null || true
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="sqlite:///${AF_DB_DIR}/airflow.db"

# --- API / UI server ---------------------------------------------------------------------
# Each user gets their own ports (several people share the login hosts): UI = 20000 + uid % 20000,
# worker / triggerer log servers = UI+1 / UI+2. Override in local.env (jsingh9's copy keeps 8081).
# BASE_URL matters: Airflow 3 tasks reach the execution API through it.
_PDFLOW_BASE_PORT=$(( 20000 + $(id -u) % 20000 ))
export AIRFLOW_PORT="${AIRFLOW_PORT:-$_PDFLOW_BASE_PORT}"
export AIRFLOW__API__PORT="$AIRFLOW_PORT"
export AIRFLOW__API__HOST="127.0.0.1"
export AIRFLOW__API__BASE_URL="http://127.0.0.1:${AIRFLOW_PORT}"
# worker/triggerer log servers: off the defaults 8793/8794 used by the other (freq_hist) Airflow
export AIRFLOW__LOGGING__WORKER_LOG_SERVER_PORT="${AIRFLOW__LOGGING__WORKER_LOG_SERVER_PORT:-$(( AIRFLOW_PORT + 1 ))}"
export AIRFLOW__LOGGING__TRIGGER_LOG_SERVER_PORT="${AIRFLOW__LOGGING__TRIGGER_LOG_SERVER_PORT:-$(( AIRFLOW_PORT + 2 ))}"

# --- The flow package + its fixed config -------------------------------------------------
export PYTHONPATH="$PDFLOW_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PDFLOW_FIXED="${PDFLOW_FIXED:-$PDFLOW_ROOT/config/fixed.yaml}"
