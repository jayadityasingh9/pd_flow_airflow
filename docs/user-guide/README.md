# raven_pd_flow — how to use it, step by step

The tool takes one Raven module from RTL build through Synopsys Fusion Compiler to `route_opt`. You fill a web
form; it copies the RM kit into your scratch, patches it, runs every step on a pd host under your account, grades
each step from the logs and reports, pauses twice for your review, and leaves a `summary.md` per run.

You run **your own Airflow** under **your own account**. Jobs use your ssh keys, your FC licenses, your scratch.

All Linux commands below are csh, typed on **mipscs85**. `P` is the shared install:

```csh
set P = /home/jsingh9.scratch/mywork/pd_flow_airflow
```

---

## Step 1 — Check your account

```csh
ls -d /home/$USER.scratch
df -h /home/$USER.scratch
```

**What happens:** nothing yet; you confirm you have a scratch area with free space (10 GB for debug/trf, 50 GB for
cic, 150 GB for corvi). Runs will be created under `/home/$USER.scratch/raven_local_ws/`.

## Step 2 — Passwordless ssh to the pd hosts

```csh
ls ~/.ssh/id_ed25519.pub                       # if missing:  ssh-keygen -t ed25519   (Enter at every prompt, no passphrase)
mkdir -p ~/.ssh; chmod 700 ~/.ssh
cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys; chmod go-w ~
foreach h (mipspd81 mipspd82 mipspd83 mipspd84 mipspd85)
  ssh -o StrictHostKeyChecking=accept-new $h hostname
end
```

**What happens:** your public key is added to your own `authorized_keys` (one file, shared by every host through
NFS), and each pd host's identity is accepted once. From now on `ssh mipspd8x` needs no password. Airflow needs
this because it starts every build and FC step with `ssh -o BatchMode=yes`, where a prompt would be a failure.
The loop must print five host names and nothing else.

## Step 3 — Create your settings

```csh
bash $P/bin/pdflow-user-setup.sh
```

**What happens:** `~/.pdflow/local.env` is written with your UI port (`20000 + uid % 20000`), the two log-server
ports, your instance directory `~/.pdflow/home`, the shared Python environment and a node-local database path.
`~/.pdflow/local.yaml` is written with commented examples (`mips_home`, `runs_root`, `fc_hosts`). Then `doctor`
runs and prints one line per check. Fix every **FAIL** (ssh is the usual one, step 2). "Airflow not running" as
**WARN** is expected at this point. Edit `~/.pdflow/local.yaml` only if you want your own Perforce workspace.

## Step 4 — Start your Airflow

```csh
tmux new -s pdflow "bash $P/airflow/start.sh"
```

**What happens:** inside a tmux session (so it survives your logout) `start.sh` creates your metadata database on
this host's local disk on first start, creates the two pools (`pd_fc`, `pd_build`), unpauses the DAG, locks your
generated admin password file to mode 600 and starts Airflow at low priority. A banner shows your **port**, the
tunnel command and the password file. Allow two minutes. Detach with **Ctrl-b** then **d**; return with
`tmux attach -t pdflow`. Confirm with `bash $P/bin/pdflow.sh doctor` (every line PASS).

## Step 5 — Open the tunnel from your PC

On **Windows**, open PowerShell (prompt `PS C:\...>`, not a Linux window) and type, with your port:

```powershell
ssh -L <port>:127.0.0.1:<port> <you>@mipscs85.mipstec.com
```

**What happens:** you log in to mipscs85 as usual, and this connection also forwards `localhost:<port>` on your PC
to the Airflow UI on mipscs85, which listens only on its own loopback. Keep this window open while you work; it is
the tunnel. Enter your password unless you did the Windows key setup in page 2. (PuTTY: Connection → SSH →
Tunnels, source `<port>`, destination `127.0.0.1:<port>`.)

## Step 6 — Log in

Browser on your PC: `http://localhost:<port>`. User `admin`. Password:

```csh
cat ~/.pdflow/home/simple_auth_manager_passwords.json.generated
```

**What happens:** Airflow generated this password for your instance on first start; nobody else has it. You see
one DAG, `raven_pd_flow`.

## Step 7 — Trigger a run

**raven_pd_flow → Trigger** (top right). Required: **pd host** and **Module**. Usual choices: host `mipspd84`
(least loaded, most free RAM), clock and build config left blank (module defaults), **Continue past DFT Rule S19**
on, both gates on. Leave **Start from** = `build`, **Stop after** = `route_opt`.

**What happens, box by box:**

| Box | What it does |
|---|---|
| `resolve_inputs` | turns the form into a run: run tag `<module>_<config>_<date-time>`, paths, defaults |
| `precheck` | prints load and free RAM of all five pd hosts; refuses a host short of RAM or a full scratch; checks the FC module on the chosen host |
| `prepare_workspace` | copies `rm_fc_cad` from the Perforce workspace into `/home/$USER.scratch/raven_local_ws/<run_tag>/`, patches clock, cores, low-VT and S19 in the copy, writes the step scripts |
| `build_rtl` | runs `build.py` on the pd host; produces the synthesis filelists |
| `fc_init_design` | FC reads the RTL and constraints into the design library |
| `fc_compile` | synthesis and placement (hours; corvi took 41 h) |
| `gate_compile` | **waits for you** (step 8) |
| `fc_clock_opt_cts`, `fc_clock_opt_opto` | clock tree synthesis and optimisation |
| `gate_clock_opt` | **waits for you** |
| `fc_route_auto`, `fc_route_opt` | routing and post-route optimisation |
| `finalize` | grades the run PASS/FAIL, writes `summary.md`, releases the run |

Every tool step is launched detached on the pd host over ssh and only watched from Airflow, so an Airflow restart
never kills a job. A step is graded from its log and reports, not from the exit code: markers, "Thank you for using
Fusion Compiler", fatal patterns, echoed clock and cores, reports present, open nets after `route_opt`. Other
`Error:` IDs become warnings. A step is stopped only when hung (no log output for 6 h) or past a backstop.

## Step 8 — Answer the gates

When a gate box turns **awaiting input**: click it → **Required Actions** (or **Browse → Required Actions**).

**What happens:** you see requested vs confirmed clock and cores, setup/hold WNS/TNS/NVP, utilisation, area, the
worst path groups, Error IDs from the log and report paths. **Approve** lets the next step start. **Reject** ends
the run as failed; you can continue it later (step 10). No answer within 72 h fails the run. From a terminal:

```csh
bash $P/bin/pdflow.sh gates
bash $P/bin/pdflow.sh review  <run_tag>
bash $P/bin/pdflow.sh approve <run_tag> -m "comment"
```

## Step 9 — Watch and read the result

```csh
bash $P/bin/pdflow.sh list --live                # your runs and live jobs
bash $P/bin/pdflow.sh status <run_tag>           # per-step status, host, hours
bash $P/bin/pdflow.sh qor <run_tag>              # WNS / TNS / hold / utilisation per step
tail -f /home/$USER.scratch/raven_local_ws/<run_tag>/rm_fc_cad/<module>/rm_fc/logs/<step>.log
```

**What happens:** in the UI a running step logs a heartbeat every 5 minutes plus every error, license and S19 line
from the FC log. At the end, `summary.md` in the run directory has the verdict, the per-step table, gate decisions
and all paths; `run.json` holds every input and fact; FC logs are in `rm_fc_cad/<module>/rm_fc/logs/`, reports in
`rm_fc_cad/<module>/rm_fc/rpts/<step>/`.

## Step 10 — Continue, redo or stop a run

| You want to | Do |
|---|---|
| continue after a reject, a stop, or a failure you fixed in the run copy | Trigger again: same **Run tag**, same **Module**, **Start from** = the step to run next; S19 on again if needed. Earlier steps are reused, not re-run. |
| redo one failed step without a new trigger | click its box → **Clear task** |
| change the clock | Trigger with Start from = `init_design` and the new clock |
| stop a running step | box → **Mark as failed** (kills the FC job on the pd host) or `bash $P/bin/pdflow.sh kill <run_tag>` |
| run a step by hand | `ssh mipspd84` then `csh -f /home/$USER.scratch/raven_local_ws/<run_tag>/scripts/30_compile.csh` |

**What happens:** on a resume, `precheck` verifies the earlier steps' results in the run directory and `prepare_workspace`
re-applies the patches; the gated steps that were not re-run are auto-approved. If Airflow was down while a job
finished, resuming **at** that step adopts and grades the finished result instead of re-running it.

## Step 11 — Stop, restart, keep alive

```csh
bash $P/airflow/stop.sh                                   # your instance only; FC jobs keep running
tmux new -s pdflow "bash $P/airflow/start.sh"              # start again; running tasks re-attach within ~20 min
bash $P/airflow/watchdog.sh install                        # optional: cron restarts your Airflow if it dies or the host reboots
```

**What happens:** `stop.sh` stops only the processes of your instance on this host. After a restart, tasks that
were watching a job are retried and re-attach to it, or adopt its finished result. The watchdog adds two lines to
your crontab (every 5 min, and after reboot); `watchdog.sh off` before planned maintenance, `on` after.

---

Details per topic: [prerequisites](01-prerequisites.md) · [passwordless ssh, incl. Windows](02-passwordless-ssh.md) ·
[setup, own clone](03-setup-and-start.md) · [UI access, PuTTY, VNC, error table](04-access-the-ui.md) ·
[every form field, resume rules](05-run-a-flow.md) · [troubleshooting](06-troubleshooting.md) · [FAQ](07-faq.md) ·
[maintainers](08-maintainers.md). Operator reference: `../../README.md`; how the DAG works inside: `../../FLOW.md`.
