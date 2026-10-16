#!/usr/bin/env bash
# Keep this checkout's Airflow running on THIS host (cron-driven, no root needed).
#
#   bash watchdog.sh install   # cron: health check every 5 min + start after a reboot (this host only)
#   bash watchdog.sh remove    # take the cron lines out again
#   bash watchdog.sh status    # cron lines, Airflow health, last log lines
#   bash watchdog.sh check     # one check now (what cron runs): restart Airflow if it is down/unhealthy
#   bash watchdog.sh off|on    # maintenance switch: 'off' = checks run but restart nothing
#
# A restart = stop.sh (writes the pdflow_shutdown flag, so step operators leave FC jobs running) then
# start.sh inside tmux session "pdflow" (tmux attach -t pdflow to look at it). Orphaned task instances are
# retried by the new scheduler within ~20 min and re-attach to their still-running FC jobs.
# Log: <instance>/watchdog.<host>.log (instance = AIRFLOW_HOME, default airflow/home). Why cron and not systemd --user: user units die at logout unless
# `loginctl enable-linger` (root) is set; cron is already enabled for users on these hosts.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
HOST="$(hostname -s)"
LOG="$AIRFLOW_HOME/watchdog.$HOST.log"        # in the instance dir: writable by its owner even on a shared checkout
TAG="# pdflow-watchdog $HERE"
OFF="$AIRFLOW_HOME/watchdog.off"
STAMP="$AIRFLOW_HOME/watchdog.last_restart"
GRACE_S=600            # after a (re)start, give standalone this long before judging it again
CRON_CHECK="*/5 * * * * bash $HERE/watchdog.sh check >> $LOG 2>&1   $TAG"
CRON_BOOT="@reboot sleep 120; bash $HERE/watchdog.sh check >> $LOG 2>&1   $TAG"

ts() { date '+%F %T'; }
health() {   # prints the unhealthy component names (empty = all good); rc 1 = API not answering
  "$VPY" - "$AIRFLOW__API__BASE_URL" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/api/v2/monitor/health", timeout=15) as r:
        d = json.load(r)
except Exception:
    sys.exit(1)
print(" ".join(k for k, v in d.items() if isinstance(v, dict) and v.get("status") != "healthy"))
PY
}
procs() { pgrep -u "$(id -un)" -f "airflow" 2>/dev/null | while read -r p; do
            { tr '\0' ' ' < "/proc/$p/environ"; } 2>/dev/null | grep -q "AIRFLOW_HOME=$AIRFLOW_HOME" && echo "$p"; done | wc -l; }

start_detached() {
  tmux kill-session -t pdflow 2>/dev/null || true
  rm -f "$AIRFLOW_HOME/pdflow_shutdown"
  if command -v tmux >/dev/null; then
    tmux new -d -s pdflow "bash -c 'bash $HERE/start.sh 2>&1 | tee -a $AIRFLOW_HOME/standalone.$HOST.log'"
  else
    setsid nohup bash "$HERE/start.sh" >> "$AIRFLOW_HOME/standalone.$HOST.log" 2>&1 < /dev/null &
  fi
  date +%s > "$STAMP"
}

case "${1:-status}" in
  install)
    ( crontab -l 2>/dev/null | grep -vF "$TAG"; echo "$CRON_CHECK"; echo "$CRON_BOOT" ) | crontab -
    echo "installed on $HOST:"; crontab -l | grep -F "$TAG"
    echo "log: $LOG   maintenance switch: bash $0 off|on"
    ;;
  remove)
    crontab -l 2>/dev/null | grep -vF "$TAG" | crontab - ; echo "removed from $HOST's crontab" ;;
  off)  date > "$OFF"; echo "watchdog OFF ($OFF exists): checks run but restart nothing. 'bash $0 on' to re-enable." ;;
  on)   rm -f "$OFF"; echo "watchdog ON" ;;
  status)
    echo "== cron on $HOST:"; crontab -l 2>/dev/null | grep -F "$TAG" || echo "   (not installed here: bash $0 install)"
    [ -f "$OFF" ] && echo "== MAINTENANCE: $OFF exists since $(cat "$OFF") -- no automatic restarts"
    bad="$(health)"; rc=$?
    if [ $rc -ne 0 ]; then echo "== Airflow: API not answering on $AIRFLOW__API__BASE_URL ($(procs) processes)"
    elif [ -n "$bad" ]; then echo "== Airflow: UNHEALTHY: $bad"
    else echo "== Airflow: all components healthy on $AIRFLOW__API__BASE_URL ($(procs) processes, tmux: $(tmux ls 2>/dev/null | grep -c pdflow) session)"; fi
    [ -f "$STAMP" ] && echo "== last watchdog restart: $(date -d @"$(cat "$STAMP")" '+%F %T')"
    [ -f "$LOG" ] && { echo "== last log lines ($LOG):"; tail -5 "$LOG"; }
    ;;
  check)
    if [ -f "$OFF" ]; then exit 0; fi
    bad="$(health)"; rc=$?
    if [ $rc -eq 0 ] && [ -z "$bad" ]; then exit 0; fi
    now=$(date +%s); last=$(cat "$STAMP" 2>/dev/null || echo 0)
    if [ $((now - last)) -lt $GRACE_S ]; then
      echo "$(ts) not healthy yet ($( [ $rc -ne 0 ] && echo 'API down' || echo "$bad")) but restarted $((now - last)) s ago: waiting"; exit 0
    fi
    if [ $rc -ne 0 ]; then why="API not answering, $(procs) airflow processes"; else why="unhealthy: $bad"; fi
    echo "$(ts) $why -> restarting Airflow on $HOST"
    bash "$HERE/stop.sh" 2>&1 | sed 's/^/    /'
    sleep 5
    start_detached
    echo "$(ts) started (tmux pdflow); FC jobs on the pd hosts untouched; orphaned tasks re-attach after retry"
    ;;
  *) echo "usage: $0 install|remove|status|check|off|on"; exit 2 ;;
esac
