# 6. Troubleshooting

First command, always:

```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh doctor
```

It checks your Python, the venv, the code version, your instance dir, the password file mode, the DB, Airflow's
health, the DAG (paused / stale), the pools, the Perforce source, your run folder and space, ssh to all five pd hosts
and the FC environment on one of them. Each FAIL has a one-line fix.

## Airflow itself

| Symptom | Cause | What to do |
|---|---|---|
| UI has no **Trigger** button, or runs stay *queued* | scheduler or DAG processor died (login-server freeze; SQLite "database is locked") or the DAG is paused/stale | `bash airflow/status.sh`; `bash airflow/stop.sh` then start again. `watchdog.sh install` does this automatically. Running FC jobs are **not** affected. |
| Runs stay *queued* on a brand-new instance, tasks never start | the DB has no `pd_fc` / `pd_build` pools | `start.sh` creates them on every start since 2026-10-15; `doctor` row "pools". Manually: `bash airflow/af.sh pools set pd_fc 2 x` and `pd_build 2 x` |
| `…/venv/bin/python: No such file or directory` | the host has no `/usr/bin/python3.11` (mipspd82–85) | run Airflow on mipscs85 / mipspd86 / mipspd81 |
| Port in use / tunnel fails | another process on your port | change `AIRFLOW_PORT` (and the two log ports) in `~/.pdflow/local.env`, restart |
| Task log: "task stopped by Airflow … NOT killing the job" | Airflow lost contact (freeze/restart) | nothing: the task is retried and re-attaches (log says *REATTACHED* / *ADOPTED*) |
| `tmux new … 'cmd 2>&1 \| tee …'` dies at once | tmux runs the command in csh | wrap it: `tmux new -s pdflow "bash -c '…'"` or use the plain form from page 3 |
| Watchdog restarted Airflow while I was stopping it | the watchdog is on | `bash airflow/watchdog.sh off` before maintenance, `on` after |

## Steps and the FC flow

| Symptom | Cause | What to do |
|---|---|---|
| precheck: *"locked by Airflow run … on host X"* | an earlier run of the same tag is active, or ran on another host | finish/fail it there; if it is dead, `rm <run>/.pdflow.lock` |
| precheck WARN *"start_from=X RE-RUNS that step"* | you resumed **at** a step that already finished and was graded | use the next step unless you really want to redo it (corvi compile = 41 h) |
| precheck: *"host has only N GB RAM available"* | the pd host is short of memory for this module | pick the host the message suggests |
| precheck: *"only N GB free on …"* | not enough scratch for this module (corvi 150 GB) | delete old runs or set `runs_root` elsewhere |
| init_design / any FC log **empty for 5–10 min** | fc_shell start-up / license queue (up to 12 h) | wait; the task log says *[waiting for license/start-up]* |
| compile FAILS: *"Rule S19 violation exists and hence exiting"* | the RM stops on DFT Rule S19 | resume: same Run tag, Start from `compile`, **Continue past S19** on |
| corvi init_design: VER-294 / VER-260, unbound modules | an RTL macro FC cannot parse (`RVN_EXCTYPE_SIZE`) | fixed automatically in the run copy (`rtl_fixes`); log shows *rtl fix … applied* |
| compile exit code 139 (segfault) | host short of RAM, swap full (seen on mipspd83) | precheck now refuses such hosts; pick another host and resume from `compile` |
| *"Host key verification failed"* | first ssh to that host under that name | `ssh <host>` once and answer `yes` (page 2 A.3) |
| *"Permission denied (publickey)"* in a task | passwordless ssh not set up for your account | page 2 A |
| *"run … is X, not Y"* on resume | wrong module | pick the run's own module |
| Run tag rejected | spaces inside the tag | copy it exactly; leading/trailing spaces are fine |
| Gate waits forever | gates need a human | approve/reject (UI or `pdflow.sh approve`); 72 h timeout fails the run |
| `pdflow.sh list` shows `running?` for an old run | its last Airflow run never finalized | cosmetic; `status <tag>` shows the real step states |

## Reporting a problem

```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh report <run_tag>
#   report written: /home/<you>.scratch/raven_local_ws/<run_tag>/pdflow_report_<time>.tar.gz
```

Send the path (it is on shared NFS) to the owner with: which run, which task (red box), which host, what you
expected. The bundle has `summary.md`, `run.json`, `patches/`, `status/`, `qor/`, the Airflow task logs of that run,
the last 300 lines of each FC/build log, `doctor` output and the code version. No design databases.

If Airflow itself misbehaves, also send `bash airflow/status.sh` output and the last 200 lines of your tmux window
(`tmux attach -t pdflow`).
