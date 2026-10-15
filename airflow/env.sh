#!/usr/bin/env bash
# Shared environment for the Raven PD-flow Airflow install.  Source this from the other scripts:
#   source env.sh
#
# Everything is isolated under the project directory:
#   airflow/venv/   the Airflow install (isolated Python venv, built by setup.sh)
#   airflow/home/   AIRFLOW_HOME (default instance dir): airflow.cfg, logs, generated admin password
#   airflow/dags/   the DAG(s)
#   pdflow/         the flow logic (plain Python, imported by the DAG; on PYTHONPATH below)
#   config/fixed.yaml  every predefined (non per-run) value
#
# Run directories are NOT here -- they live under runs_root in config/fixed.yaml
# (/home/<user>.scratch/raven_local_ws/<run_tag>/ by default).
#
# Two ways to use one checkout (see SHARING.md / TEAM_ROLLOUT.md):
#   * your own clone: overrides in airflow/local.env + config/local.yaml (git-ignored), instance = airflow/home
#   * a SHARED read-only install: overrides in ~/.pdflow/local.env + ~/.pdflow/local.yaml, instance =
#     ~/.pdflow/home (PDFLOW_INSTANCE) -- created by bin/pdflow-user-setup.sh. Several people can then run
#     their own Airflow (own port, own DB, own logs, own password) from the same code.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Nothing this Airflow creates (run dirs, generated scripts executed on the pd hosts) is group-writable.
umask 022
export PDFLOW_ROOT="$(cd "$HERE/.." && pwd)"

# Overrides, lowest to highest priority (all optional, all git-ignored):
#   1. $HERE/local.env            per-checkout (the owner of this clone)
#   2. ~/.pdflow/local.env        per-user (wins; needed when the checkout is shared), or $PDFLOW_LOCAL_ENV
# Keys: AIRFLOW_PORT, AF_DB_DIR, VENV, PDFLOW_INSTANCE, AIRFLOW__API__HOST, ... -- see local.env.example
[ -f "$HERE/local.env" ] && source "$HERE/local.env"
PDFLOW_LOCAL_ENV="${PDFLOW_LOCAL_ENV:-$HOME/.pdflow/local.env}"
if [ -f "$PDFLOW_LOCAL_ENV" ]; then
  _p0="${AIRFLOW_PORT:-}"; _w0="${AIRFLOW__LOGGING__WORKER_LOG_SERVER_PORT:-}"; _t0="${AIRFLOW__LOGGING__TRIGGER_LOG_SERVER_PORT:-}"
  source "$PDFLOW_LOCAL_ENV"
  # a user file that moves the UI port but not the log ports must not inherit the checkout's explicit log
  # ports (they would collide with the checkout owner's instance): fall back to UI+1 / UI+2 below.
  if [ "${AIRFLOW_PORT:-}" != "$_p0" ]; then
    [ "${AIRFLOW__LOGGING__WORKER_LOG_SERVER_PORT:-}" = "$_w0" ] && unset AIRFLOW__LOGGING__WORKER_LOG_SERVER_PORT
    [ "${AIRFLOW__LOGGING__TRIGGER_LOG_SERVER_PORT:-}" = "$_t0" ] && unset AIRFLOW__LOGGING__TRIGGER_LOG_SERVER_PORT
  fi
  unset _p0 _w0 _t0
fi

# --- Interpreter Airflow itself runs on ---------------------------------------------------
# System python3.11: its sqlite3 supports the deterministic functions Airflow's metadata DB
# needs (the freeware pythons do not). Verified on mipscs82/mipscs85/mipspd81.
export AF_BASE_PY="${AF_BASE_PY:-/usr/bin/python3.11}"
export AIRFLOW_VERSION="3.3.2"
export PYMAJMIN="3.11"

export VENV="${VENV:-$HERE/venv}"
export VPY="$VENV/bin/python"
export AIRFLOW_BIN="$VENV/bin/airflow"

# --- The instance: AIRFLOW_HOME (airflow.cfg with its secrets, task logs, admin password, shutdown flag)
# One per running Airflow. Default = this checkout's airflow/home. Two Airflows started from the SAME
# checkout on two hosts would share it (secrets, logs, the stop.sh flag) -- give each its own
# PDFLOW_INSTANCE (bin/pdflow-user-setup.sh puts it in ~/.pdflow/home).
export PDFLOW_INSTANCE="${PDFLOW_INSTANCE:-$HERE/home}"
export AIRFLOW_HOME="$PDFLOW_INSTANCE"
export AIRFLOW__CORE__DAGS_FOLDER="$HERE/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES="False"
# A fresh DB would create the DAG PAUSED: triggered runs then sit "queued" forever.
export AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION="False"
# --- Footprint on a shared host (measured 2026-10-15 on mipscs85 with the defaults: 20 processes, 2.3 GB RSS,
# ~2.4 % of one core idle). LocalExecutor pre-forks PARALLELISM idle workers (~130 MB each): 6 is plenty
# (pools allow 2 FC + 2 build tool steps at once; setup tasks are seconds; gates wait in the triggerer).
export AIRFLOW__CORE__PARALLELISM="${AIRFLOW__CORE__PARALLELISM:-6}"
# scheduler: sleep 5 s between idle loops instead of 1 s (nothing here is time-critical at the second level)
export AIRFLOW__SCHEDULER__SCHEDULER_IDLE_SLEEP_TIME="${AIRFLOW__SCHEDULER__SCHEDULER_IDLE_SLEEP_TIME:-5}"
# one DAG file: one parser process, re-parsed every 2 min (code changes still land within 2 min)
export AIRFLOW__DAG_PROCESSOR__PARSING_PROCESSES="1"
export AIRFLOW__DAG_PROCESSOR__REFRESH_INTERVAL="600"
# one API worker is enough for a handful of users
export AIRFLOW__API__WORKERS="${AIRFLOW__API__WORKERS:-1}"
# start.sh runs `airflow standalone` under nice 10 / best-effort low I/O priority (PDFLOW_NICE to change)
export PDFLOW_NICE="${PDFLOW_NICE:-10}"
# DAG parsing on a loaded host: normal parse is ~1.5 s, but frozen login servers took 50 s and hit
# the 50 s processor timeout -> DAG marked stale -> no Trigger button. Give it room, and do not
# mark the DAG stale after one slow/killed parse.
export AIRFLOW__CORE__DAGBAG_IMPORT_TIMEOUT="240"
export AIRFLOW__DAG_PROCESSOR__DAG_FILE_PROCESSOR_TIMEOUT="300"
export AIRFLOW__DAG_PROCESSOR__STALE_DAG_THRESHOLD="1800"
export AIRFLOW__DAG_PROCESSOR__MIN_FILE_PROCESS_INTERVAL="120"
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
export AF_DB_DIR="${AF_DB_DIR:-/var/tmp/$(id -un)_pdflow_airflow_db}"
mkdir -p "$AF_DB_DIR" 2>/dev/null || true
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="sqlite:///${AF_DB_DIR}/airflow.db"

# --- API / UI server ---------------------------------------------------------------------
# Each user gets their own ports (several people share the login hosts): UI = 20000 + uid % 20000,
# worker / triggerer log servers = UI+1 / UI+2. Override in local.env (jsingh9's copy keeps 8081).
# BASE_URL matters: Airflow 3 tasks reach the execution API through it.
# HOST / BASE_URL can be overridden too (SHARING.md way B: 0.0.0.0 + the host's name) -- the default
# binds to loopback only, so the UI is reachable just through an ssh tunnel.
_PDFLOW_BASE_PORT=$(( 20000 + $(id -u) % 20000 ))
export AIRFLOW_PORT="${AIRFLOW_PORT:-$_PDFLOW_BASE_PORT}"
export AIRFLOW__API__PORT="$AIRFLOW_PORT"
export AIRFLOW__API__HOST="${AIRFLOW__API__HOST:-127.0.0.1}"
export AIRFLOW__API__BASE_URL="${AIRFLOW__API__BASE_URL:-http://127.0.0.1:${AIRFLOW_PORT}}"
# worker/triggerer log servers: off the defaults 8793/8794 used by the other (freq_hist) Airflow
export AIRFLOW__LOGGING__WORKER_LOG_SERVER_PORT="${AIRFLOW__LOGGING__WORKER_LOG_SERVER_PORT:-$(( AIRFLOW_PORT + 1 ))}"
export AIRFLOW__LOGGING__TRIGGER_LOG_SERVER_PORT="${AIRFLOW__LOGGING__TRIGGER_LOG_SERVER_PORT:-$(( AIRFLOW_PORT + 2 ))}"

# --- The flow package + its fixed config -------------------------------------------------
export PYTHONPATH="$PDFLOW_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PDFLOW_FIXED="${PDFLOW_FIXED:-$PDFLOW_ROOT/config/fixed.yaml}"
# Per-user config overlay: $PDFLOW_LOCAL, else ~/.pdflow/local.yaml if it exists, else config/local.yaml
# (resolved in pdflow/config.py; exported here so every tool sees the same choice).
if [ -z "${PDFLOW_LOCAL:-}" ] && [ -f "$HOME/.pdflow/local.yaml" ]; then
  export PDFLOW_LOCAL="$HOME/.pdflow/local.yaml"
fi
