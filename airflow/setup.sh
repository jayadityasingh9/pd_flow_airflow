#!/usr/bin/env bash
# One-time setup: build an isolated venv and install Apache Airflow (pinned version + the
# official constraints file for this Python), then create the metadata DB and the pools.
#
#   bash setup.sh
#
# Safe to re-run: it upgrades in place. Remove everything with:  rm -rf venv home
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./env.sh

echo "== Airflow $AIRFLOW_VERSION into $VENV (base python: $AF_BASE_PY, host $(hostname)) =="
[ -x "$AF_BASE_PY" ] || { echo "FATAL: base interpreter not found: $AF_BASE_PY" >&2; exit 1; }

if [ "$VENV" != "$HERE/venv" ] && [ -x "$VPY" ]; then
  # VENV points at someone else's / a shared install (local.env): use it, don't modify it
  echo "-- using existing venv $VENV (set in local.env); skipping the install"
  SKIP_INSTALL=1
elif [ ! -x "$VPY" ]; then
  echo "-- creating venv from $AF_BASE_PY"
  "$AF_BASE_PY" -m venv "$VENV"
fi

echo "-- verifying this venv's sqlite supports deterministic functions"
"$VPY" - <<'EOF'
import sqlite3, sys
sqlite3.connect(":memory:").create_function("f", 0, lambda: 1, deterministic=True)
print("   sqlite OK (lib %s, python %s)" % (sqlite3.sqlite_version, sys.version.split()[0]))
EOF

if [ -z "${SKIP_INSTALL:-}" ]; then
  echo "-- upgrading pip/wheel"
  "$VPY" -m pip install --quiet --upgrade pip wheel
  CONSTRAINTS="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYMAJMIN}.txt"
  echo "-- installing apache-airflow==$AIRFLOW_VERSION (+pyyaml, pytest) with constraints"
  echo "   $CONSTRAINTS"
  "$VPY" -m pip install "apache-airflow==${AIRFLOW_VERSION}" pyyaml pytest --constraint "${CONSTRAINTS}"
fi

echo "-- airflow version check"
"$AIRFLOW_BIN" version

echo "-- initialising metadata DB under $AF_DB_DIR"
"$AIRFLOW_BIN" db migrate
hostname > "$AF_DB_DIR/HOST"

echo "-- pools (limit concurrent tool jobs)"
"$AIRFLOW_BIN" pools set pd_fc 2 "Concurrent Fusion Compiler steps (each ~16 cores + 1 FC license)"
"$AIRFLOW_BIN" pools set pd_build 2 "Concurrent pyBuildSim RTL builds"

echo
echo "DONE. Next:  tmux new -s pdflow 'bash $HERE/start.sh'   then the tunnel it prints (port $AIRFLOW_PORT)"
echo "      Check your setup any time:  bash $PDFLOW_ROOT/bin/pdflow.sh doctor"
