# 3. Setup and start

Two ways. **Way A′** (shared install) is recommended: no clone, no install, always the current code.
**Way A** (your own clone) is for people who want to change the code.

---

## Way A′ — your own Airflow from the shared install (1 minute)

On **mipscs85** (csh). Any host with `/usr/bin/python3.11` works; mipscs85 is the one with the most RAM.

```csh
# 1. one-time: writes ~/.pdflow/local.env + ~/.pdflow/local.yaml, then runs doctor
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow-user-setup.sh
```

What it created:

| File | Contents | Edit when |
|---|---|---|
| `~/.pdflow/local.env` | your UI port (`20000 + uid % 20000`), the two log-server ports (+1, +2), `PDFLOW_INSTANCE=$HOME/.pdflow/home`, `VENV=<shared venv>`, `AF_DB_DIR=/var/tmp/<you>_pdflow_airflow_db` | the port collides with something (change `AIRFLOW_PORT` and the two log ports) |
| `~/.pdflow/local.yaml` | commented examples: `runs_root`, `mips_home`, `fc_hosts` | you want your own Perforce workspace (`mips_home`), another run folder, or fewer hosts in the form |
| `~/.pdflow/home/` | your Airflow instance: `airflow.cfg`, task logs, admin password (created on first start) | never by hand |

```csh
# 2. read doctor's output; fix every FAIL (page 2 for ssh). WARN "not running" is expected here.

# 3. start Airflow inside tmux (so it keeps running after you log out)
tmux new -s pdflow 'bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/start.sh'
```

`start.sh` prints a banner with **your port**, the tunnel command and the password file, creates your metadata DB on
first start (`/var/tmp/<you>_pdflow_airflow_db`, local to that host), creates the pools, unpauses the DAG and locks
the password file to mode 600. Give it about two minutes. Detach from tmux with **Ctrl-b** then **d**; come back
with `tmux attach -t pdflow`.

```csh
# 4. verify (from any shell on that host)
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh doctor
#    expect: "N checks: N PASS, 0 WARN, 0 FAIL"
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/status.sh
```

Then page 4 for the browser.

### Stop / restart

```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/stop.sh      # stops ONLY your instance on this host
tmux new -s pdflow 'bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/start.sh'
```

Stopping Airflow never stops FC jobs: they run detached on the pd hosts. After a restart, running tasks are retried
automatically within about 20 minutes and **re-attach** to their jobs (or adopt the finished result).

### Keep it running without you (optional)

```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/watchdog.sh install
```

Adds two lines to **your** crontab on that host: a health check every 5 minutes and a start after a reboot. If the
API is down or any component (scheduler, triggerer, DAG processor) is unhealthy, it restarts your Airflow. No root,
nothing system-wide. `watchdog.sh status | off | on | remove`. Before you stop Airflow on purpose, run
`watchdog.sh off`, otherwise it comes back within 5 minutes. Log: `~/.pdflow/home/watchdog.<host>.log`.

### Rules of the shared install

- You can read everything there and write nothing. All your writes go to `~/.pdflow/`, `/var/tmp` and your scratch.
- When the owner publishes a code update, your running Airflow picks it up at the next DAG parse (≤ 2 minutes).
  If `airflow/env.sh` changed, restart your Airflow.
- Your runs, gates and history are yours; nobody else sees them in their UI.

---

## Way A — your own clone (developers)

```csh
cd /home/$USER.scratch
git clone /home/jsingh9.scratch/git/pd_flow_airflow.git
cd pd_flow_airflow

# optional settings (git-ignored); only the keys you change
cp config/local.yaml.example config/local.yaml
cp airflow/local.env.example airflow/local.env

# install Airflow 3.3.2 into airflow/venv (~5 min, ~300 MB, needs PyPI + raw.githubusercontent.com)
#   shortcut without internet: put  export VENV=/home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/venv
#   into airflow/local.env and setup.sh will reuse the shared venv read-only
bash airflow/setup.sh

bash bin/pdflow.sh doctor
tmux new -s pdflow 'bash /home/'$USER'.scratch/pd_flow_airflow/airflow/start.sh'
```

Differences from A′: your instance is `airflow/home/` inside the clone (not `~/.pdflow/home`), settings are
`airflow/local.env` and `config/local.yaml` in the clone, and updates come with `git pull` + restart.
Everything else (ports, DB, runs, tunnel, password) behaves the same.

Run the unit tests any time: `bash -c 'source airflow/env.sh; $VENV/bin/python -m pytest -q -p no:cacheprovider tests'`.
