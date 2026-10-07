# Raven PD flow — Airflow (RTL build → Fusion Compiler route_opt)

> One Airflow run takes a Raven module from RTL to **route_opt**. You pick the pd host, the
> module and a few knobs in the trigger form; everything else is predefined. The flow pauses
> twice (after **compile** and after **clock_opt_opto**) so you can review QoR and
> **Approve / Reject** before it goes on.
> The Perforce workspace is **never modified**: every run works on its own fresh copy.
>
> **Sharing with colleagues / testing:** see [`SHARING.md`](SHARING.md) (clone, `doctor`, error `report`).

**Contents**
1. [Start / stop Airflow](#1-start--stop-airflow)
2. [Run the flow (the trigger form)](#2-run-the-flow-the-trigger-form)
3. [Review gates](#3-review-gates)
4. [What a run leaves on disk](#4-what-a-run-leaves-on-disk)
5. [Resume, retry, kill](#5-resume-retry-kill)
6. [Fixed (predefined) settings](#6-fixed-predefined-settings)
7. [How a step is judged PASS / FAIL](#7-how-a-step-is-judged-pass--fail)
8. [Files in this project](#8-files-in-this-project)
9. [Verify it yourself](#9-verify-it-yourself)
10. [Known limits](#10-known-limits)

---

## 1. Start / stop Airflow

Airflow runs on the host where you start it (now **mipscs83**; mipscs82 kept freezing). Its metadata DB is
**node-local** (`/var/tmp/jsingh9_pdflow_airflow_db`), so always start it on the same host; on a new
host `start.sh` creates an empty DB (runs on disk are unaffected and can be resumed).

```csh
# start (inside tmux so it survives logout; FC runs take hours)
tmux new -s pdflow 'bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/start.sh'
#   detach: Ctrl-b d      re-attach: tmux attach -t pdflow

# on your laptop: tunnel, then open http://127.0.0.1:8081
ssh -L 8081:127.0.0.1:8081 jsingh9@mipscs83.mipstec.com
#   user: admin   password: cat /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/home/simple_auth_manager_passwords.json.generated

# status / stop
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/status.sh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/stop.sh
```

Stopping Airflow does **not** stop FC jobs: they run detached on the pd host. After `start.sh`,
**Clear** the running task in the UI and it re-attaches to the job (or picks up its result if it
finished meanwhile).

Port **8081** (the older freq_hist Airflow uses 8080). One-time install was `bash airflow/setup.sh`.

## 2. Run the flow (the trigger form)

UI → DAG **raven_pd_flow** → **Trigger** (form). Or from the command line:

```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/af.sh dags trigger raven_pd_flow \
     --conf '{"fc_host":"mipspd81","module":"debug_module"}'
```

| Field | Default | What it does |
|---|---|---|
| **pd host** (`fc_host`) | *required* | mipspd81 … mipspd85. The build and every FC step run there (over ssh). precheck prints the live load / free RAM of all 5 hosts. pd81/82 = 128 cores, pd83-85 = 32. |
| **Module** | *required* | corvi_module / cic_module / debug_module / trf_module → sets DESIGN_NAME, top RTL, default config and clock. On a **resume** pick the run's own module (a different one is refused). |
| **Build config** | module default | `-c`/`SYN_CONFIG`, e.g. `2Core_4VC_32K`, `2Core_4VC_32K_rvn_drive` (the 41 dirs in `$MIPS_HOME/raven/src/config`). |
| **Clock period (ns)** | module default | Patches `set Clk_period` in the run copy's `design_setup.tcl` (drives every functional clock). Defaults: corvi 1.25, cic 1.25, debug 2, trf 1.25. |
| **Max cores** | 16 | `set_host_options -max_cores` (patched in `fc_setup.tcl` — the one that takes effect — and `design_setup.tcl`). ≤ cores of the host. |
| **Continue past DFT Rule S19** | off | The RM's `compile.tcl` **exits** when DFT DRC reports Rule S19 (nonscan cell disturb) — debug_module (5×) and trf_module (1×) both hit it. **On** = the run copy only warns, compile continues, and the S19 count shows in the compile gate. **Off** = RM behaviour (compile fails). |
| **Start from** | build | `build` = new run. Any later step = **resume** an existing run (needs Run tag). |
| **Stop after** | route_opt | Last step to run. |
| **Run tag** | automatic | `<module>_<config>_<date-time>`. Needed to resume. |
| **Review QoR after compile / after clock_opt_opto** | on / on | The two gates. |
| **Dry run** | off | New runs only: workspace + patches + scripts + prechecks, no tools. |

Steps in order: `build → init_design → compile → clock_opt_cts → clock_opt_opto → route_auto → route_opt`
(`place_opt` is part of compile in this RM's unified flow).

## 3. Review gates

When the run reaches a gate the task turns **awaiting input**. Open it (Graph → `gate_compile` or
`gate_clock_opt` → *Required Actions*). You see: requested vs. confirmed clock and cores, setup/hold
WNS/TNS/NVP, utilization, area, the 5 worst path groups, Error IDs seen in the log, report paths.

**From the terminal instead** (same effect as the UI; recorded as user `admin` with your comment):

```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh gates                  # what is waiting
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh review  <run_tag>      # QoR table of the gate
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh approve <run_tag> -m "WNS ok for now"
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh reject  <run_tag> -m "fix floorplan first"
```

* **Approve** → the next step starts.
* **Reject** → the run stops and ends **failed**. Continue later with the same Run tag and
  `start_from` = the next step (the already-reviewed gate is skipped).
* No answer in 72 h → the gate fails the run.

Gates are skipped automatically when switched off, in a dry run, when the gated step was reused
(resume) or when nothing runs after it.

## 4. What a run leaves on disk

```
/home/jsingh9.scratch/raven_local_ws/
  runs_index.tsv                       one line per finished Airflow run
  <run_tag>/
    summary.md                         PASS/FAIL, per-step table, gates, paths   <- start here
    run.json                           every input + fixed-config snapshot + per-step status/facts
    patches/<module>.diff              exactly what was changed in the copy
    scripts/NN_<step>.csh              the step scripts -- re-runnable by hand on a pd host
    scripts/env_fc.csh env_build.csh   tool environments
    status/<step>.{pid,start,rc,end}   written by the job itself
    logs/build.console.log  <step>.wrapper.log
    qor/<step>.json                    parsed QoR
    rm_fc_cad/                         the fresh copy of $MIPS_HOME/rm_fc_cad
      vbuild/<config>/build.log        RTL build
      <module>/rm_fc/logs/<step>.log   FC logs
      <module>/rm_fc/rpts/<step>/      FC reports
      <module>/rm_fc/tmp/              markers + <DESIGN>.nlib
```

Command line helpers:

```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh list            # runs + live jobs
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh status <run_tag>
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh qor <run_tag>
```

## 5. Resume, retry, kill

| Situation | What to do |
|---|---|
| A step failed; you fixed something **in the run copy** | UI: that task → **Clear** (+ downstream). Same run, same tag. |
| Continue a stopped / rejected / finished run | New trigger: same **Run tag**, same **Module**, **Start from** = next step (build config and clock are taken from the run when left blank). |
| Change the clock of an existing run | Resume with **Start from = init_design** (the SDC is read there). |
| Airflow died while FC was running | `start.sh`, then **Clear** the task → it re-attaches. |
| Stop a running step | UI: task → **Mark failed** (kills the whole remote job: TERM, KILL after 60 s), or `bash .../bin/pdflow.sh kill <run_tag>`. If the Airflow API is unhealthy at that moment the job is deliberately **not** killed (so an Airflow hiccup can never kill a 20 h route_opt) — use `pdflow.sh kill`. |
| Run a step by hand, outside Airflow | `ssh mipspd8x` then `csh -f <run>/scripts/30_compile.csh` |

## 6. Fixed (predefined) settings

All in `config/fixed.yaml` (a snapshot is stored in every `run.json`):

| Setting | Value |
|---|---|
| Source (read only) | `/home/jsingh9/Perforce/jsingh9_raven_ws2` ( `$MIPS_HOME` ) → `rm_fc_cad` copied per run |
| Runs | `/home/jsingh9.scratch/raven_local_ws/<run_tag>/` |
| FC | `module load fc/2022.12-SP6`; license queuing on, wait ≤ 12 h (`SNPSLMD_QUEUE`, `SNPS_MAX_WAITTIME/QUEUETIME=43200`) |
| Build env | `source $MIPS_HOME/raven/source_env.csh $MIPS_HOME/raven` (+ `IMGSITE=SC`) |
| Build | `build.py ravenRTL -t <top> --configName <cfg> --workDirectory <cfg> -bt Synthesis -tech gf_22fdxp_9t_autopro150_9m_emram_stack -f <gf22 lvt std cells .v> -f <std_primitives.v>` |
| TECH_DIR | `GF_22FDXP_9T_AutoPro150_9M_eMRAM_stack` |
| FC call | `fc_shell -f ../../scripts_block/rm_fc_scripts/<step>.tcl` in `<module>/rm_fc/tmp` (not `make`: its `\| tee` hides failures and it silently re-runs prerequisites) |
| Tool environment | clean (`env -i`), started with `setsid` on the pd host → no DISPLAY, no stray variables |
| Soft timeouts | per module size, e.g. S: compile 4 h, route_opt 4 h; L (corvi): compile 24 h. Counted from when fc_shell becomes **active** — time spent queueing for a license (up to 12 h) is not counted. |

| Module | DESIGN_NAME | Default config | Default clock | Floorplan |
|---|---|---|---|---|
| corvi_module | corvi_module | 2Core_4VC_32K | 1.25 ns | corvi_module.def |
| cic_module | cm3_cic | 2Core_4VC_32K_rvn_drive | 1.25 ns | cm3_cic.def |
| debug_module | rvn_dbu_top_wrapper | 2Core_4VC_32K_rvn_drive | 2 ns | rvn_dbu_top_wrapper.def |
| trf_module | rvn_mips_l2trf_top | 2Core_4VC_32K_rvn_drive | 1.25 ns | automatic |

## 7. How a step is judged PASS / FAIL

Exit codes alone are not trusted (the RM scripts keep going after Tcl errors, and `build.py`
exits 0 in some failure cases). A step **FAILS** if any of:

* **Build**: exit ≠ 0 · `build.log` does not end with `Build Completed` · `Error-[` / `- ERROR -` /
  Traceback in the log · `sim.Linux/sim` missing · `synthesis_{rtl,inc,pkg}.f` missing/empty/stale or
  not listing the top file · peakrdl non-zero return code.
* **FC**: exit ≠ 0 · the step's marker (written by the last line of `<step>.tcl`) missing or stale →
  the script stopped early (e.g. *No clocks created*, *Rule S19*) · no *Thank you for using Fusion
  Compiler* (crash/kill) · a fatal pattern (ELAB/VER/LNK/NDM errors, license failure, segfault …) ·
  FC echoed a different `Clk_period` / `-max_cores` than requested · the report_qor clock period for
  group `Clk` ≠ requested · expected reports missing.

Error IDs are counted from FC's own `print_message_info -ids * -summary` table (so errors inside
redirected commands count too); ELAB/VER/LNK/NDM errors fail the step. route_opt fails on any
**open net**; DRC counts are warnings (limit configurable: `max_route_opt_drcs`). The `Clk` path
group must exist in report_qor with the requested period.

Other `Error:` IDs and `RM-error` lines are **warnings**, listed in the gate and in `summary.md`.
With *Continue past DFT Rule S19* on, the S19 stop becomes a warning too (`DFT Rule S19 violated (N×) -- continued …`, details in `rpts/compile/dft_drc_full.rpt`).

## 8. Files in this project

```
/home/jsingh9.scratch/mywork/pd_flow_airflow/
  README.md  PLAN.md
  config/fixed.yaml                 predefined settings
  airflow/dags/raven_pd_flow_dag.py the DAG (form + task wiring)
  airflow/{setup,start,stop,status,af,env}.sh
  pdflow/                           the flow logic (plain Python, unit-tested)
    context.py   form -> resolved run        precheck.py  read-only checks
    workspace.py run dir, run.json, lock     patch.py     design_setup/fc_setup edits
    scripts.py   generated csh scripts       remote.py    ssh to pd hosts
    runner.py    launch / re-attach / tail   checks.py    PASS/FAIL rules
    qor.py       report parsing + gate text  summary.py   summary.md, runs_index
    airflow_ops.py  the step + gate operators    cli.py   bin/pdflow.sh
  bin/pdflow.sh
  tests/test_pdflow.py  tests/fixtures/
```

## 9. Verify it yourself

```csh
cd /home/jsingh9.scratch/mywork/pd_flow_airflow
bash -c 'source airflow/env.sh; airflow/venv/bin/python -m pytest -q -p no:cacheprovider tests'
#   expect: all passed
bash airflow/af.sh dags list-import-errors
#   expect: No data found
bash airflow/af.sh dags test raven_pd_flow --conf '{"fc_host":"mipspd81","module":"debug_module","dry_run":true,"clock_period_ns":1.5,"max_cores":8}'
#   expect: "run PASSED"; then:
diff -rq /home/jsingh9/Perforce/jsingh9_raven_ws2/rm_fc_cad /home/jsingh9.scratch/raven_local_ws/dry-debug_<...>/rm_fc_cad
#   expect: only debug_module design_setup.tcl and fc_setup.tcl differ
```

## 10. Known limits

* The metadata DB is tied to the host running `start.sh` (SQLite must not live on NFS).
* One module per run (trigger several runs to do several modules; up to 2 FC steps run at once —
  pool `pd_fc`).
* A non-default build config still uses the module's fixed DEF/SDC (precheck warns).
* Scope ends at route_opt (no chip_finish / write_data / StarRC / PrimeTime).
* Timeouts are placeholders until real runtimes are known — adjust `timeouts_h` in `config/fixed.yaml`.
