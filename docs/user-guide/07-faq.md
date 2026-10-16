# 7. FAQ

**Whose disk space and account does a run use?**
Yours. The run directory is under `/home/<you>.scratch/raven_local_ws/`, the FC job runs under your account on the
pd host with your licenses, your metadata DB is in `/var/tmp/<you>_…` on the Airflow host, your instance (config,
logs, password) is `~/.pdflow/home/`. The only thing read from the owner's area is the Perforce workspace
(`mips_home`), read-only, until you set your own in `~/.pdflow/local.yaml`.

**Do I share the owner's Airflow?**
No. You run your own instance on your own port with its own database. You see only your runs; the owner sees only
theirs. The code and the Python environment are shared, read-only.

**Is a new password needed?**
Yes, and it is automatic. On your first `start.sh`, Airflow generates an admin password into
`~/.pdflow/home/simple_auth_manager_passwords.json.generated` (mode 600). User name is always `admin`. Nobody has
to give you a password and yours is not in git.

**Which port is mine?**
`20000 + (your uid % 20000)`, printed by `start.sh`; also `grep AIRFLOW_PORT ~/.pdflow/local.env`. The two log
servers use port+1 and port+2. If a neighbour's uid is within 2 of yours you collide; `doctor` warns and you set
other ports in `~/.pdflow/local.env`.

**Can several of us run on mipscs85 at the same time?**
Yes. Each instance is about 2 GB RAM and 2 % of a core, runs at low priority (`nice 10`), and has its own port and DB.

**How do I get code updates?**
With the shared install you get them automatically: your running Airflow re-reads the DAG folder every 2 minutes.
If the owner says `env.sh` changed, restart your Airflow. Clone users: `git pull`, then restart.

**What about FC licenses?**
Each instance allows 2 concurrent FC steps and 2 concurrent builds (its pools). With several users that is 2 per
person, not 2 in total, so coordinate if licenses are tight.

**Does the owner syncing Perforce affect my run?**
If you use the default `mips_home`, FC reads RTL from that workspace at `init_design`, so a sync during your run
can change what is read. Use your own workspace (`mips_home` in `~/.pdflow/local.yaml`) to be independent.

**Where does the Airflow history go if I start on another host?**
It stays in the old host's `/var/tmp` DB (and ages out after 30 idle days). Every run dir has its own `run.json`;
`pdflow.sh list|status|qor` read those, so nothing about the runs themselves is lost. Resume from the new host with
Start from = the next step (page 5, "Resume").

**Why does the UI only listen on 127.0.0.1?**
Because it can launch shell jobs as you. The ssh tunnel is your authentication. Do not expose it on the network
or through internet tunnels (ngrok and the like).

**Can I run a step by hand?**
Yes: `ssh mipspd84` then `csh -f /home/<you>.scratch/raven_local_ws/<run>/scripts/30_compile.csh`. The scripts
are self-contained and set up the same environment Airflow uses.

**What is in git, and what is not?**
Code, docs, tests, examples. **Not**: venvs, `airflow/home` (configs, passwords, logs), `local.env`,
`config/local.yaml`, run data. Clone: `/home/jsingh9.scratch/git/pd_flow_airflow.git`.
