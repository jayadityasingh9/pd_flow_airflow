#!/usr/bin/env bash
# pdflow command line (works from csh):  bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh <cmd>
#   list [--live] | status <run_tag> | qor <run_tag> [step] | kill <run_tag> [step]
#   gates | review <run_tag> | approve <run_tag> [-m comment] | reject <run_tag> [-m comment]
#   doctor | report <run_tag>
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/airflow/env.sh"
exec "$VENV/bin/python" -m pdflow.cli "$@"
