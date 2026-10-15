#!/usr/bin/env bash
# OPTIONAL relay, only when your PC cannot ssh to the Airflow host directly (the normal way is one direct
# tunnel: ssh -N -L 8081:127.0.0.1:8081 <you>@mipscs85). Keeps an ssh tunnel 127.0.0.1:<port> (this host) ->
# 127.0.0.1:<port> (the Airflow host) alive, so a Windows tunnel that lands on THIS host reaches the UI.
#
#   bash ui-tunnel.sh ensure    # open the tunnel if it is not there (idempotent; what cron runs)
#   bash ui-tunnel.sh install   # cron on this host: 'ensure' every 5 min (+ after reboot)
#   bash ui-tunnel.sh remove    # remove the cron lines
#   bash ui-tunnel.sh status
#   bash ui-tunnel.sh stop      # close the tunnel
#
# Airflow host / port: PDFLOW_AIRFLOW_HOST (default mipscs85; set it in local.env) and AIRFLOW_PORT from env.sh / local.env.
# Needs passwordless ssh from this host to the Airflow host (same NFS home + ~/.ssh/authorized_keys).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
AH="${PDFLOW_AIRFLOW_HOST:-mipscs85}"
PORT="$AIRFLOW_PORT"
FWD="$PORT:127.0.0.1:$PORT"
TAG="# pdflow-ui-tunnel $HERE"
LOG="$HERE/ui-tunnel.$(hostname -s).log"

running() { pgrep -u "$(id -un)" -f "ssh .*-L $FWD .*$AH" >/dev/null 2>&1; }
case "${1:-status}" in
  ensure)
    if running; then exit 0; fi
    if curl -s -m 5 -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
      echo "$(date '+%F %T') port $PORT on $(hostname -s) is already served by something else (another tunnel?); leaving it"; exit 0; fi
    ssh -f -N -o BatchMode=yes -o ConnectTimeout=15 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -L "$FWD" "$AH" && echo "$(date '+%F %T') tunnel opened on $(hostname -s): 127.0.0.1:$PORT -> $AH:$PORT"
    ;;
  stop)   pkill -u "$(id -un)" -f "ssh .*-L $FWD" && echo "tunnel closed" || echo "no tunnel" ;;
  install)
    ( crontab -l 2>/dev/null | grep -vF "$TAG"
      echo "*/5 * * * * bash $HERE/ui-tunnel.sh ensure >> $LOG 2>&1   $TAG"
      echo "@reboot sleep 90; bash $HERE/ui-tunnel.sh ensure >> $LOG 2>&1   $TAG" ) | crontab -
    bash "$0" ensure; echo "installed on $(hostname -s):"; crontab -l | grep -F "$TAG" ;;
  remove) crontab -l 2>/dev/null | grep -vF "$TAG" | crontab -; echo "removed from $(hostname -s)'s crontab" ;;
  status)
    echo "== $(hostname -s): tunnel 127.0.0.1:$PORT -> $AH:$PORT"
    running && echo "   ssh tunnel process: running" || echo "   ssh tunnel process: NOT running"
    code=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null); echo "   UI answers on 127.0.0.1:$PORT: HTTP ${code:-none}"
    echo "   cron: $(crontab -l 2>/dev/null | grep -cF "$TAG") line(s)   log: $LOG" ;;
  *) echo "usage: $0 ensure|install|remove|status|stop"; exit 2 ;;
esac
