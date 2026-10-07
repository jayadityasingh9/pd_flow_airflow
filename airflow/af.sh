#!/usr/bin/env bash
# Run any `airflow` CLI command with this project's environment (handy from csh, where
# `source env.sh` does not work):
#   bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/af.sh dags list
#   bash .../af.sh dags test raven_pd_flow --conf '{"fc_host":"mipspd81","dry_run":true}'
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
export PATH="$VENV/bin:$PATH"
exec "$AIRFLOW_BIN" "$@"
