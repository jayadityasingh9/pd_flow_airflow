# 5. Run a flow

## The trigger form

UI → **raven_pd_flow** → **Trigger**. Only the first two fields are required.

| Field | Default | What it does |
|---|---|---|
| **pd host** | *required* | `mipspd81 … mipspd85`: where the build and every FC step run. Precheck prints the live load / free RAM of all five and refuses a host with too little RAM for the module (debug/trf 16 GB, cic 64 GB, corvi 200 GB); the error names a better host. |
| **Module** | *required* | `corvi_module`, `cic_module`, `debug_module`, `trf_module`. Sets DESIGN_NAME, top RTL, default build config and clock. On a resume pick the run's own module. |
| **RTL source** | Run build.py | `build` = fresh filelists from `build.py` · `existing` = reuse another run's `synthesis_*.f` (its `-tech` must match the Technology node, else refused) · `filelist` = your own RM-style Tcl. `pdflow.sh builds` lists reusable builds. |
| **Build path** | blank | for `existing` (a run folder, full path) or `filelist` (absolute path of the `.tcl`) |
| **Technology node** | 9T eMRAM stack | RM tech dir + matching `build.py -tech`. All modules have `GF_22FDXP_9T_AutoPro150_9M_eMRAM_stack`; corvi also `GF_22FDXP_9T_AutoPro150`, `GF_22FDXP_6P75T_116A`, `GF_22FDXP_6P75T`, `SYN_5FF_6TMVT`. Fixed for the life of a run. |
| **Build config** | module default | `-c`/`SYN_CONFIG`; corvi `2Core_4VC_32K`, others `2Core_4VC_32K_rvn_drive`; must be a dir in `$MIPS_HOME/raven/src/config` |
| **Clock period (ns)** | module default | corvi 1.25, cic 1.25, debug 2, trf 1.25. Patches `set Clk_period` in the run copy. |
| **Max cores** | 16 | `set_host_options -max_cores`; must not exceed the host's cores |
| **Max low-VT cells (%)** | 10 | `set_multi_vth_constraint -low_vt_percentage`; takes effect from compile |
| **Continue past DFT Rule S19** | off | the RM `exit`s on S19 (debug 5×, trf 1×, corvi 38× hit it). **On** = warn and continue. Not remembered between triggers: set it every time. |
| **Start from** | build | `build` = new run; anything later = **resume** an existing run (needs Run tag) |
| **Stop after** | route_opt | last step to run |
| **Run tag** | automatic | `<module>_<config>_<date-time>`; needed to resume (copy it from `pdflow.sh list`) |
| **Review QoR after compile / after clock_opt_opto** | on / on | the two gates |
| **Dry run** | off | new runs only: workspace, patches, scripts and prechecks, no tools. Use it first. |

## Test ladder for a new user

| # | What | Form | Expect | Time |
|---|---|---|---|---|
| 1 | form check | leave pd host empty | `resolve_inputs` fails at once with "fc_host is required" | seconds |
| 2 | dry run | `mipspd84`, `debug_module`, **Dry run** on | 13 green boxes, nothing launched | 1 min |
| 3 | small real run | `debug_module`, **Stop after** `compile`, **S19** on | build ✓ init_design ✓ compile ✓ (WARN is fine) | ~20 min |
| 4 | full run | `debug_module`, Stop after `route_opt`, gates on | pauses at `gate_compile` and `gate_clock_opt`; ends PASS | ~1.5 h |
| 5 | your module | corvi / cic / trf | corvi compile alone took 41 h at 16 cores | hours to days |

## Gates (the two human stops)

When the run reaches `gate_compile` or `gate_clock_opt`, the box turns **awaiting input**. Click it → **Required
Actions**. You see requested vs confirmed clock and cores, setup/hold WNS/TNS/NVP, utilization, area, the worst
path groups, Error IDs seen in the log and report paths. **Approve** continues; **Reject** ends the run failed
(resume later with Start from = the next step). No answer in **72 h** fails the run.

From a terminal (same effect):

```csh
set P = /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh
bash $P gates                              # what is waiting
bash $P review  <run_tag>                  # the QoR table of its gate
bash $P approve <run_tag> -m "WNS ok for now"
bash $P reject  <run_tag> -m "fix floorplan first"
```

Gates are skipped automatically for dry runs, when switched off, when the gated step was reused (resume past it),
or when nothing runs after it.

## Watching a run

```csh
bash $P list --live                        # your runs + live tool jobs (host:pid, since when)
bash $P status <run_tag>                   # per-step status, host, runtime, history
bash $P qor <run_tag>                      # parsed QoR of the finished steps
tail -f /home/$USER.scratch/raven_local_ws/<run_tag>/rm_fc_cad/<module>/rm_fc/logs/<step>.log
```

In the UI, a running step's **Logs** show a heartbeat line every 5 minutes with elapsed time and log size, plus
every `Error`, `RM-info`, license and S19 line from the FC log. "[waiting for license/start-up]" during the first
minutes is normal (license queueing can take up to 12 h; it is not counted against any limit).

A step is stopped only when it is **hung** (no log growth for 6 h) or past a backstop (48 h for debug/trf,
120 h cic, 240 h corvi). The final grade is not the exit code: markers, "Thank you for using Fusion Compiler",
fatal patterns, echoed clock/cores, reports present, open nets after route_opt all count. Other `Error:` IDs
become warnings listed at the gate.

## What a run leaves on disk

```
/home/<you>.scratch/raven_local_ws/
  runs_index.tsv                       one line per finished Airflow run
  <run_tag>/
    summary.md                         PASS/FAIL, per-step table, gates, paths   <- start here
    run.json                           every input + per-step status/facts (the source of truth)
    patches/<module>.diff              exactly what was changed in the RM copy
    scripts/NN_<step>.csh              re-runnable by hand:  ssh mipspd8x; csh -f .../scripts/30_compile.csh
    status/<step>.{pid,start,rc,end}   written by the job itself
    qor/<step>.json                    parsed QoR
    rm_fc_cad/                         your copy of $MIPS_HOME/rm_fc_cad
      vbuild/<config>/build.log        RTL build
      <module>/rm_fc/logs/<step>.log   FC logs      <module>/rm_fc/rpts/<step>/   FC reports
```

## Resume, retry, kill

| Situation | What to do |
|---|---|
| A step failed and you fixed something in the run copy | UI: that box → **Clear task** (+ downstream). Same run. |
| Continue a stopped / rejected / finished run | New trigger: same **Run tag**, same **Module**, **Start from** = the next step. Config and clock are taken from the run when left blank. Set **S19** again if you need it. |
| Change the clock of an existing run | Resume with Start from = `init_design` and the new clock |
| Airflow died or moved host while a step ran | wait for the step to finish (`pdflow.sh list --live`), then new trigger with Start from = **that** step: the finished, never-graded result is adopted and graded, not re-run. If it was already graded (status ok/warn), use the next step; precheck warns otherwise. |
| precheck: "locked by Airflow run … on host X" | that Airflow run is still active, or it ran on another host whose DB cannot be seen from here. If it is dead: `rm <run>/.pdflow.lock` and trigger again. |
| Stop a running step | UI: box → **Mark as failed** (TERM, then KILL after 60 s), or `bash $P kill <run_tag>` |
| Run a step by hand, outside Airflow | `ssh mipspd84` then `csh -f <run>/scripts/30_compile.csh` (the log goes to the usual place; `run.json` is not updated) |

## Reusing a build (RTL source = existing)

`bash $P builds` lists finished builds under your runs with module, config, `-tech` and date. Give the full run path
as **Build path**. Rules: the build's `-tech` must equal the chosen Technology node; the copied filelists still
point into the source run and into its Perforce workspace, so do not delete that run while this one is alive.
