#!/usr/bin/env bash
# One-time setup for a user of a SHARED raven_pd_flow install (no clone, no pip, no venv copy).
#
#   bash <shared-install>/bin/pdflow-user-setup.sh
#
# Creates ~/.pdflow/ with:
#   local.env   your Airflow ports (20000 + uid % 20000, +1, +2), your instance dir (~/.pdflow/home), the
#               shared venv, a node-local metadata DB dir -- read by airflow/env.sh after the checkout's local.env
#   local.yaml  your flow settings (commented: mips_home, runs_root, fc_hosts) -- read by pdflow/config.py
# then runs `pdflow.sh doctor`. Safe to re-run: existing files are kept (use --force to rewrite them).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG="$HOME/.pdflow"
FORCE=0; [ "${1:-}" = "--force" ] && FORCE=1
mkdir -p "$CFG/home"
PORT=$(( 20000 + $(id -u) % 20000 ))

if [ ! -f "$CFG/local.env" ] || [ "$FORCE" = 1 ]; then
  cat > "$CFG/local.env" <<EOF
# raven_pd_flow: $(id -un)'s Airflow instance settings (written by pdflow-user-setup.sh $(date +%F)).
# Read by $ROOT/airflow/env.sh (after the checkout's own airflow/local.env; these win).
export AIRFLOW_PORT=$PORT                                  # UI / API (ssh tunnel: -L $PORT:127.0.0.1:$PORT <airflow-host>)
export AIRFLOW__LOGGING__WORKER_LOG_SERVER_PORT=$((PORT + 1))
export AIRFLOW__LOGGING__TRIGGER_LOG_SERVER_PORT=$((PORT + 2))
export PDFLOW_INSTANCE=\$HOME/.pdflow/home                   # AIRFLOW_HOME: airflow.cfg, task logs, admin password
export VENV=$ROOT/airflow/venv                             # the shared install's venv (read-only for you)
export AF_DB_DIR=/var/tmp/\$(id -un)_pdflow_airflow_db        # metadata DB: node-local, never NFS
EOF
  echo "wrote $CFG/local.env"
else
  echo "kept  $CFG/local.env (use --force to rewrite)"
fi

if [ ! -f "$CFG/local.yaml" ] || [ "$FORCE" = 1 ]; then
  cat > "$CFG/local.yaml" <<EOF
# raven_pd_flow: $(id -un)'s flow settings (written by pdflow-user-setup.sh $(date +%F)).
# Only the keys you change; everything else comes from $ROOT/config/fixed.yaml.
# \${USER} / \${HOME} and \${other_key} are expanded.

# Where your runs go (default: /home/\${USER}.scratch/raven_local_ws; corvi needs ~150 GB free):
#runs_root: /home/\${USER}.scratch/raven_local_ws

# The Perforce workspace the flow COPIES rm_fc_cad from and reads RTL from (never modified).
# Default: the install owner's workspace. Use your own to run your own RTL / scripts:
#mips_home: /home/\${USER}/Perforce/<your_workspace>

# Limit the pd hosts offered in the trigger form:
#fc_hosts: [mipspd81, mipspd84]
EOF
  echo "wrote $CFG/local.yaml"
else
  echo "kept  $CFG/local.yaml (use --force to rewrite)"
fi
chmod 700 "$CFG" "$CFG/home"

cat <<EOF

Your Airflow (runs as you, on this host, with your ssh keys and licenses):
  start:   tmux new -s pdflow 'bash $ROOT/airflow/start.sh'      (detach: Ctrl-b d)
  tunnel:  ssh -f -N -L $PORT:127.0.0.1:$PORT $(id -un)@$(hostname -f)
  UI:      http://127.0.0.1:$PORT   user admin, password: cat $CFG/home/simple_auth_manager_passwords.json.generated
  CLI:     bash $ROOT/bin/pdflow.sh list|status|qor|gates|approve|doctor|report ...
  stop:    bash $ROOT/airflow/stop.sh      (FC jobs on the pd hosts keep running; a restart re-attaches)
  keep-alive (optional, cron on that host):  bash $ROOT/airflow/watchdog.sh install
  your files: $CFG/local.env  $CFG/local.yaml  $CFG/home/ (airflow.cfg, task logs, password, watchdog log)

Checking your setup now (expect WARNs for 'not running' / 'DAG not registered' until you start Airflow):
EOF
bash "$ROOT/bin/pdflow.sh" doctor || true
