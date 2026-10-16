# 8. Maintainer notes (for the owner of the shared install)

## Publishing code

```csh
cd /home/jsingh9.scratch/mywork/pd_flow_airflow
bash -c 'source airflow/env.sh; $VENV/bin/python -m pytest -q -p no:cacheprovider tests'   # all green first
git add -A; git commit -m "..."
git push shared main              # the bare repo colleagues clone from: /home/jsingh9.scratch/git/pd_flow_airflow.git
```

Every instance started from the shared checkout (way A′) sees the new DAG/pdflow code at its next DAG parse
(≤ 2 min) and uses it for tasks that start afterwards; a task already running keeps the code it loaded. Changes to
`airflow/env.sh` or `start.sh` need each instance restarted. Clone users (`way A`) run `git pull`.

Before pushing anywhere **outside the MIPS network** (public GitHub etc.): the repo contains internal host names, IPs,
NFS paths, library paths and the structure of a proprietary flow. `SHARING.md` way E says no; an internal GitHub
Enterprise is fine.

## The owner's own instance

- Host: **mipscs85** (since 2026-10-15), tmux session `pdflow`, UI 8081 (`airflow/local.env` pins 8081/8795/8796).
- Keep-alive: `bash airflow/watchdog.sh install` on that host (cron, every 5 min + `@reboot`); `watchdog.sh off`
  before planned maintenance.
- Low footprint settings live in `env.sh` (6 executor workers, 1 API worker, 1 parser every 2 min, 5 s idle sleep)
  and `start.sh` (`nice 10`, `ionice -c2 -n7`). Measured idle: 18 processes, 2.0 GB RSS, 2 % of one core.
- Moving host: `watchdog.sh off; stop.sh; watchdog.sh remove` on the old host; `tmux new -s pdflow 'bash .../start.sh'`
  on the new one (fresh DB; pools and unpause are automatic); `watchdog.sh install`; update `PDFLOW_AIRFLOW_HOST`
  in `local.env` and the docs. Runs continue from any host via `run.json`.

## Hosts that can run Airflow

Need `/usr/bin/python3.11` (the venv is bound to it): mipscs85, mipspd86, mipspd81, mipscs82, mipscs83.
**Not** mipspd82–85 (only python 3.6/3.12). The login servers mipscs82/83 froze under load and their SQLite
scheduler died twice ("database is locked"); the watchdog is the mitigation, a compute host the alternative.

## Security points

- Password files are 0600 (`start.sh` enforces; `doctor` checks). Anyone who can read one can run jobs as that user.
- The API binds to `127.0.0.1` only. `AIRFLOW__API__HOST=0.0.0.0` in `local.env` opens it to the intranet
  (demo only; everything then runs as the owner).
- Generated csh scripts run with `umask 022` under `env -i` on the pd hosts.

## Known limits (2026-10-16)

- One `admin` login per instance (SimpleAuthManager); no per-user roles. A team service with LDAP needs IT.
- SQLite on node-local disk; no HA. `/var/tmp` has a 30-day tmpfiles rule for idle files.
- `max_runtime_h` (S 48 / M 120 / L 240) and `stall_timeout_h` (6) are backstops; corvi compile measured 41 h.
- Core dumps go to systemd-journal (root only).
- Report the `RVN_EXCTYPE_SIZE` RTL macro and the 38 S19 async-reset flops in `biu_mmr_regblk` upstream.
