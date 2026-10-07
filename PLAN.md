# PLAN — Raven PD flow on Apache Airflow (RTL build → FC route_opt)

**Updated:** 2026-09-29. At the start of implementation, this file is copied to `/home/jsingh9.scratch/mywork/pd_flow_airflow/PLAN.md`.

## Context

Today, getting a Raven module from RTL to route_opt means doing everything by hand:
1. `cp -rf $MIPS_HOME/rm_fc_cad <ws>`
2. `source raven/source_env.csh`
3. `source vbuild/example.build`
4. edit `design_setup.tcl`
5. run `make init_design … route_opt` step by step, passing TECH_DIR, DESIGN_NAME and SYN_CONFIG each time
6. read the reports

The files contain several traps:
- The Makefile defaults are wrong for 3 of the 4 modules.
- `| tee` hides fc_shell failures.
- `sh_continue_on_error true` lets a step with errors still write its "done" marker.

The goal is an Airflow DAG that does all of this in one run:
- asks you only for the per-run values, in the trigger form;
- takes everything else from one predefined config file;
- pauses for your review of the QoR after compile and after clock_opt_opto;
- never touches the Perforce workspace.

**Your decisions:**

| Topic | Decision |
|---|---|
| Modules | all 4 modules, one chosen per run |
| Review | trigger form plus two QoR approval gates |
| Knobs in the form | clock period, build config, start-from and stop-at step, max cores |
| Airflow install | fresh install in a new directory, with its own venv |
| FC host | FC runs on a host you pick from mipspd81–85 |
| FC version | fc/2022.12-SP6 |
| Licenses | queue for a license, waiting up to 12 h |

## 1. What gets built

```
Trigger form (you) ──► resolve_inputs ─► precheck ─► prepare_workspace ─► build_rtl ─► fc_init_design ─► fc_compile
                                                                                                              │
             ┌────────────────────────────────────────────────────────────────────────────────────────────────┘
             ▼
        gate_compile (Approve/Reject) ─► fc_clock_opt_cts ─► fc_clock_opt_opto ─► gate_clock_opt (Approve/Reject)
                                                                                                      │
             ┌────────────────────────────────────────────────────────────────────────────────────────┘
             ▼
        fc_route_auto ─► fc_route_opt ─► finalize (always runs; fails the DAG run if any step failed)
```

- Airflow runs on **mipscs85**, which has the verified `/usr/bin/python3.11` and a node-local sqlite DB.
- Each tool step (build and FC) runs on the **pd host you choose**. Airflow starts it over passwordless SSH as a detached job.
- The job writes its status files and logs to shared NFS, and Airflow follows them.

## 2. Running on mipspd81–85: feasibility (checked 2026-09-29)

This works. From mipscs85, `ssh -o BatchMode=yes` reaches all 5 hosts without a password.

| host | cores | RAM free | load | Rocky | `/home/jsingh9.scratch`, Perforce ws, Modules init, `fc/2022.12-SP6`, std-cell libs |
|---|---|---|---|---|---|
| mipspd81 | 128 | 397 G | 3 | 8.10 | all OK |
| mipspd82 | 128 | 135 G | 9 | 8.10 | all OK |
| mipspd83 | 32 | 272 G | 7 | 8.10 | all OK |
| mipspd84 | 32 | 297 G | 14 | 8.10 | all OK |
| mipspd85 | 32 | 343 G | 20 | 8.10 | all OK |

- The remote login shell is **csh**. The launcher is therefore a small `/bin/sh` script on NFS, run as `ssh <host> /bin/sh <run>/scripts/launch_<step>.sh`. No quoting passes through csh.
- The launcher runs `setsid env -i <clean env> /bin/csh -f <step>.csh </dev/null >logs/<step>.wrapper.log 2>&1 &`.
  - The csh script's first line writes `hostname $$` to `status/<step>.pid`. Because of `setsid`, that pid is also the process-group id.
  - Its last line writes the exit code to `status/<step>.rc`, plus `status/<step>.start` and `.end` epochs taken on the remote host.
- The FC job survives an Airflow or SSH restart. Airflow re-attaches by reading the pid file, then running `ssh host kill -0 <pid>` and checking `/proc/<pid>/cmdline`.
- To kill a job: `ssh host kill -TERM -- -<pgid>`, wait 60 s, then `-KILL`.
- **Freshness checks compare NFS file mtimes to each other** (for example, marker vs `status/<step>.start`), never to the Airflow host's clock. This avoids clock skew between hosts.
- `max_cores` must be ≤ the chosen host's `nproc`, which is 32 on pd83–85. Precheck checks this over SSH.

## 3. Inputs you give (Trigger DAG w/ config form)

| Param | Type / choices | Default | Notes |
|---|---|---|---|
| `fc_host` | enum: mipspd81 … mipspd85 | **none (required)** | Precheck logs the live load, free RAM and nproc of all 5 hosts, and warns if the chosen host is busy. |
| `module` | enum: corvi_module, cic_module, debug_module, trf_module | debug_module | The form shows the module's DESIGN_NAME and size class. |
| `build_config` | string, dropdown of the 41 dirs in `$MIPS_HOME/raven/src/config` | blank → module default | Used as `-c`, as `-w`, and as SYN_CONFIG. Precheck validates it. |
| `clock_period_ns` | number (0, 20], or null | blank → module default | Patches `set Clk_period` in the run copy. It only takes effect if the run starts at `init_design` or earlier. |
| `max_cores` | integer 1–128 | 16 | Patches `fc_setup.tcl:18` (the value that actually takes effect) and `design_setup.tcl`. The FC log is checked to confirm it applied. |
| `start_from` / `stop_at` | enum of the steps (build, init_design, compile, clock_opt_cts, clock_opt_opto, route_auto, route_opt) | build / route_opt | Must satisfy start_from ≤ stop_at. |
| `run_tag` | string `^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$` | blank → auto `<mod>_<cfg>_<ts>` | **Required** when start_from ≠ build (resume). |
| `gate_after_compile` / `gate_after_clock_opt` | boolean | true / true | |
| `dry_run` | boolean | false | Creates the workspace, patches and scripts, but runs no tools. |

Resume rules:
- `start_from=build` means a new run, and the tag must not already exist.
- Any other `start_from` requires `<tag>/run.json` to exist.
  - Module and build_config cannot change on resume.
  - The clock can change only if `start_from=init_design`.
  - max_cores and fc_host can always change.

## 4. Predefined details (`config/fixed.yaml`, the single source)

**Paths and tools**

| Key | Value |
|---|---|
| mips_home | `/home/jsingh9/Perforce/jsingh9_raven_ws2` (the source copy is only ever read) |
| runs_root | `/home/jsingh9.scratch/raven_local_ws/<run_tag>/rm_fc_cad` (a fresh copy per run, then `chmod -R u+w`, because Perforce files are `r--`) |
| module init | `/mips/tools/freeware/Modules/latest/tcl/init/tcsh` + `module use /mips/tools/freeware/modulefiles /mips/proj/tools/modulefiles` |
| FC | `module load fc/2022.12-SP6`; `setenv SNPSLMD_QUEUE true`, `SNPS_MAX_WAITTIME 43200`, `SNPS_MAX_QUEUETIME 43200` (set explicitly, because the Makefile's `export` lines are no-ops) |
| Build env | `source $MIPS_HOME/raven/source_env.csh $MIPS_HOME/raven` (build step only; it loads fc/X-2025.06-SP2, so FC steps never source it) |
| Build command | `build.py ravenRTL -t <top> --configName <CFG> --workDirectory <CFG> -bt Synthesis -tech gf_22fdxp_9t_autopro150_9m_emram_stack -f …/lvt/1.00a/verilog/3.0/gf22napllogl20hdl116a.v -f …/lvt/1.00a/verilog/std_primitives.v`, run from `$RC/vbuild` |
| TECH_DIR | `GF_22FDXP_9T_AutoPro150_9M_eMRAM_stack` |
| FC invocation | per step, in `$RC/<module>/rm_fc/tmp`: `fc_shell -f ../../scripts_block/rm_fc_scripts/<step>.tcl >& ../logs/<step>.log`, with env TECH_DIR, DESIGN_NAME and SYN_CONFIG. This skips make: no tee, no silent re-run of prerequisites, no wrong defaults. The equivalent `make` line is kept in each script as a comment. |
| Airflow | port **8081**, bound to 127.0.0.1, `AIRFLOW__API__BASE_URL=http://127.0.0.1:8081` (otherwise tasks call 8080), DB at `/var/tmp/${USER}_pdflow_airflow_db/airflow.db` |

**Module table**

| | corvi_module | cic_module | debug_module | trf_module |
|---|---|---|---|---|
| DESIGN_NAME | corvi_module | cm3_cic | rvn_dbu_top_wrapper | rvn_mips_l2trf_top |
| top file (under mips_home) | raven/src/main/corvi_module/corvi_module.sv | cm3/src/main/cm3_cic/cm3_cic.v | cm3/src/main/cm3_dbu/rvn_dbu_top_wrapper.sv | tru/src/main/rvn_mips_l2trf_top.v |
| default build_config | 2Core_4VC_32K | 2Core_4VC_32K_rvn_drive | 2Core_4VC_32K_rvn_drive | 2Core_4VC_32K_rvn_drive |
| default Clk_period | 1.25 | 1.25 | 2 | 1.25 |
| clocks driven by $Clk_period | PI_CLK | cm_clk | clk, jtag_tck, ci_dbu_tp_pclk | clk, core_clk* |
| floorplan | corvi_module.def | cm3_cic.def | rvn_dbu_top_wrapper.def | auto (no DEF) |
| size class (sets timeouts and disk/RAM prechecks) | L | M | S | S |

**Patch specs.** These are applied only to the run copy. Each must match **exactly once**; a diff goes to `patches/<module>.diff` and the result is read back to confirm.

| File | Pattern | → |
|---|---|---|
| `block_files/<TECH_DIR>/design_setup.tcl` | `^set Clk_period[ \t]+[0-9.]+[ \t]*$` | `set Clk_period <clk>` |
| `block_files/<TECH_DIR>/design_setup.tcl` | `^([ \t]*)set_host_options -max_cores [0-9]+` | `\1set_host_options -max_cores <n>` |
| `scripts_block/rm_setup/fc_setup.tcl` (line 18, sourced after design_setup) | `^set_host_options -max_cores [0-9]+` | `set_host_options -max_cores <n>` |

Clock uncertainty stays fixed. Soft step timeouts depend on size class, e.g. L: build 1 h, compile 24 h, route_opt 24 h. They start as placeholders and are retuned to 3× the measured runtimes.

## 5. How each task behaves

| Task | What it does |
|---|---|
| resolve_inputs | Fills blanks from the module defaults (or from run.json on resume), validates the combinations, and passes the resolved context to the other tasks. No disk writes. |
| precheck | Read-only. Checks that paths, top file, config dir, build.py, std-cell `-f` files, DEF, the libraries referenced in design_setup and `UNIFIED_FLOW true` all exist. Dry-runs the patches on the source (count must be 1). Probes the host over SSH: `ssh fc_host` then `csh -f` with module init and `module load fc/2022.12-SP6`, then checks `which fc_shell` and that SNPSLMD_LICENSE_FILE is set. Also checks nproc ≥ max_cores, free RAM and disk, the `$MIPS_HOME/../filelists/*_syn_rtl.tcl` override (WARN), and the run_tag rules. |
| prepare_workspace | `mkdir` (fails if it already exists) → copytree → `chmod u+w` → patches → writes `scripts/{env_build,env_fc,10_build,20_init_design…70_route_opt}.csh` plus `launch_*.sh` → writes `run.json` (all resolved inputs, a snapshot of fixed.yaml, hosts, Airflow run id) and a lock file. Every script can be re-run by hand outside Airflow. |
| build_rtl / fc_\<step\> | A custom `PdStepOperator` with four modes. **REUSE:** the step is before start_from, so it is verified and marked success. **SKIP:** the step is after stop_at. **DRY:** dry run. **RUN:** launch over SSH, or re-attach if the job is still alive, then stream filtered log lines (errors, FLW-8001/8100, a heartbeat every 5 min) into the Airflow task log. Checks run after the job ends. Before each FC step, the script deletes this step's marker and all later ones, and rotates the old log and rpts directory. Steps are ordered init_design → compile. `place_opt` is marker-only (UNIFIED_FLOW), so it is written after a successful compile. |
| gate_compile / gate_clock_opt | A subclass of `ApprovalOperator`. The body is Markdown built from the reports: setup and hold WNS/TNS/NVP, utilization, cell area, requested vs reported clock, the worst 5 path groups, and the new Error IDs. Reject fails the run. The gate waits up to 72 h. It auto-approves when the gate is off, on a dry run, or when the gated step is outside start_from..stop_at. |
| finalize | Runs whatever the upstream outcome (`all_done`). Writes `summary.md`, appends to `runs_index.tsv` and releases the lock. It **fails** if any step did not succeed, so a run with a failed step never shows green. |

`retries=0`. To retry, use "Clear task + downstream" in the UI.

## 6. Success and failure checks

**Build.** Exit code 0 alone proves nothing, because `MIPS_HOME` unset → `sys.exit()` returns 0. The build passes only if all of these hold:
- exit code == 0
- the last line of `build.log` is `build - CRITICAL - Build Completed`
- `build.log` has no `Error-[`, ` - ERROR - ` or `Traceback`
- `sim.Linux/sim` exists
- `py.Work/synthesis_{rtl,inc,pkg}.f` exist, are non-empty and fresh
- `synthesis_rtl.f` contains the top file, and its paths point into **this run's** `$RC/vbuild/<CFG>`
- `rdl_generate.log` has no `Return code: [1-9]`

Error-code hints in failure messages: 1 = VCS/yaml, 2 = config, 3 = filelist/prpl, 4 = setup, 5 = Config.pm.

**FC step:**

| # | Check | On failure |
|---|---|---|
| F1 | rc == 0 | FAIL |
| F2 | the step's marker exists and its mtime ≥ `status/<step>.start` | FAIL. The step hit a hard exit ("No clocks created", S19, missing read script), crashed, or hit a license problem. |
| F3 | "Thank you for using Fusion Compiler." is present | FAIL: crashed or killed. FC also prints this line on hard exits, so it doesn't prove success on its own. |
| F4 | fatal patterns: `No clocks created, Exiting`, `Rule S19 violation`, read-script RM-errors, `Error: … (ELAB|VER|LNK|NDM)-n`, `Current design is not defined`, `Fatal:`, segfault/core dump, license checkout failure | FAIL |
| F5 | the `set Clk_period` and `-max_cores` values echoed in the log (via `source -echo`) match what was requested | FAIL: the patch had no effect |
| F6 | the step's expected outputs exist, are non-empty and fresh (nlib; `rpts/<step>/<step>.report_qor`, `.report_global_timing`, `.qor.qor`) | FAIL |
| F7 | the clock period in report_qor equals the requested one | FAIL |
| F8 | other `^Error:` IDs and `RM-error:` lines (even a good run has some) | **WARN**; listed in the gate |

## 7. Resume and recovery

| Situation | What to do |
|---|---|
| A step failed and you fixed something in the run copy | Clear that task + downstream |
| A gate was rejected, and you want to continue later | New trigger with the same `run_tag` and `start_from=<next step>` |
| Airflow died mid-step | FC keeps running on the pd host. Run `start.sh`, then Clear the task; it re-attaches. |
| Kill a job | `bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh kill <tag>` |

## 8. Files to create

```
/home/jsingh9.scratch/mywork/pd_flow_airflow/
  PLAN.md  README.md                      README = operator guide (csh blocks, tunnel, form walk-through)
  config/fixed.yaml                       everything in §4
  pdflow/                                 plain Python, unit-testable; only airflow_ops.py imports Airflow
    config.py      load_fixed(), list_build_configs(), STEPS, step_index()
    context.py     resolve(params, fixed, run_id)->RunCtx; step_mode(); gate_needed()
    workspace.py   create_workspace(), lock/unlock, update_manifest(), verify_reusable()
    patch.py       PatchSpec(file, pattern, template, expect_count=1); apply_patches() -> raises PatchError
    scripts.py     render env_fc / env_build / build / fc_step / launch scripts; write_all()
    remote.py      ssh_launch(host, launcher), ssh_alive(host, pid, script), ssh_kill(host, pgid), host_probe(host)
    runner.py      run_or_reattach(step, ...) -> RunResult (poll the NFS status files, stream filtered log, soft timeout)
    checks.py      check_build(), scan_fc_log(), check_fc_step() -> Verdict(ok, fatal, warnings, facts)
    qor.py         parse_global_timing(), parse_report_qor(), parse_utilization(), render_markdown()
    airflow_ops.py PdStepOperator, PdQorGate(ApprovalOperator)
    cli.py         status | check | qor | kill | list        (wrapped by bin/pdflow.sh)
  airflow/
    env.sh setup.sh start.sh stop.sh status.sh af.sh      (patterned on freq_hist_jenkins/airflow)
    dags/raven_pd_flow_dag.py             thin DAG built from pdflow
    dags/hitl_smoke_dag.py                toy approval DAG for M1 only; deleted afterwards
    venv/  home/                          generated, git-ignored
  tests/  fixtures/ + test_*.py
```

**Patterns reused** from `/home/jsingh9.scratch/mywork/freq_hist_jenkins/`:
- the `env.sh` structure
- the `setup.sh` sqlite deterministic check plus `pip install apache-airflow==3.3.2 --constraint …/constraints-3.11.txt`
- `start.sh`: `standalone` with the venv on PATH, run inside **tmux** so it lives for hours
- `stop.sh`: kill by AIRFLOW_HOME in `/proc/<pid>/environ`
- Airflow 3 SDK `@dag/@task/Param`, `AirflowFailException` for fail-fast
- `precheck_inputs.py`'s exit-code and SUMMARY style
- Lessons: no `| tee`, per-subprocess env, no sqlite on NFS

## 9. Verification

```csh
cd /home/jsingh9.scratch/mywork/pd_flow_airflow
airflow/venv/bin/python -m pytest -q tests                 # unit tests: patch (1 match per module; 0/2 → error), checks, qor, scripts (tcsh -n), step-mode/gate truth tables
bash airflow/af.sh dags list-import-errors                  # expect: none
bash airflow/af.sh dags test raven_pd_flow --conf '{"fc_host":"mipspd81","module":"debug_module","dry_run":true,"clock_period_ns":1.5,"max_cores":8}'
diff -rq /home/jsingh9/Perforce/jsingh9_raven_ws2/rm_fc_cad /home/jsingh9.scratch/raven_local_ws/<dry-tag>/rm_fc_cad
#   expect: only debug_module design_setup.tcl + fc_setup.tcl differ
```

Test fixtures:
- the real `vbuild/2Core_4VC_32K/build.log`, trimmed
- the pico `fc_run.log` and its `rpts/`
- the 4 real `design_setup.tcl` and `fc_setup.tcl` files
- synthetic failure logs: no-clocks, S19, crash, ELAB error, build rc 2/3

Real runs, in order:
1. **Bad inputs:** each fails in seconds with a clear message.
2. **debug, stop_at=build** (~2 min), then check `find $MIPS_HOME -newer <run>/src_manifest.sha256 -type f` prints nothing.
3. **debug, stop_at=compile:** confirm F5 and F7 see the requested clock and cores; capture golden reports and the list of Error IDs that normally appear.
4. **Gates and resume:** Approve → continue; Reject → run fails; resume with `start_from=clock_opt_cts` bypasses the gate.
5. **Fault injection:**
   - Break the clock port in the run copy's SDC → FAIL, even though rc is 0.
   - "Mark failed" → the remote process group is gone.
   - `stop.sh` mid-step → FC is still alive → `start.sh` + Clear → the task re-attaches.
6. **Full runs:** full debug run, then trf and cic up to compile, then full corvi. Compare the numbers with a manual `make` run of the same config.

## 10. Milestones

| Milestone | Work | Verified by |
|---|---|---|
| M1 | skeleton, fresh install, UI at `http://127.0.0.1:8081` over `ssh -L 8081:127.0.0.1:8081 jsingh9@mipscs85.mipstec.com`, toy HITL DAG | UI loads; toy gate Approve/Reject works |
| M2 | pdflow library and unit tests | pytest green |
| M3 | DAG, dry run, negative tests | §9 dry-run diff; bad-input runs |
| M4 | real build and debug compile on a pd host; golden reports; error baseline | real runs 2–3 |
| M5 | gates, resume, fault injection | real runs 4–5 |
| M6 | full debug, then trf/cic, then corvi; tune timeouts | real run 6 |
| M7 | README operator guide; update PLAN status | — |

## 11. Risks

| Risk | Mitigation |
|---|---|
| FC reports "success" while Tcl errors occurred | checks F2–F8; WARNs are shown in the gate |
| Re-running a step over existing nlib labels (`copy_block` without `-force`) may keep working on the old block | tested in M5; if confirmed, add a pre-step `remove_blocks -force` |
| sqlite locking with HITL and concurrent runs | parallelism 8; pool `pd_fc`=2 |
| The build's `source_env.csh` has never been run under non-interactive `csh -f` over SSH | tested in M4 |
| The DB is tied to mipscs85, and `standalone` runs in the foreground | tmux; run.json lets a run resume from anywhere |
| Disk grows by one nlib per run (scratch is 88% full) | `pdflow.sh list` shows sizes; clean-up is manual for now |

## 12. Defaults I chose (tell me if you disagree), and what I'll ask as it comes up

Defaults:
- one module per run
- unknown `Error:` IDs are WARN
- a non-default build_config is allowed, with a WARN because the module's fixed DEF is still used
- the filelist override is WARN
- gate timeout is 72 h, then the run fails
- no email
- keep all run directories
- `dry_run` and `run_tag` are exposed in the form
- Airflow runs on mipscs85

**Questions I'll ask during implementation, when they block:**
- **M1:** the Airflow UI password handling (generated file vs a password you set).
- **M4:** whether to promote the observed Error IDs to a FAIL baseline.
- **M4:** whether any corvi `_rvn_drive` config should pick `corvi_module_rvn_drive.def`.
- **M6:** whether to add a clean-up command or email notifications.

---

## 13. Status (2026-09-30)

| Milestone | Status | Evidence |
|---|---|---|
| M1 install + UI | done | Airflow 3.3.2 fresh venv (`/usr/bin/python3.11`), standalone on mipscs82:8081 in tmux; health endpoint all healthy |
| M2 library + unit tests | done | `pytest tests` → 49 passed |
| M3 DAG, dry run, negative tests | done | dry runs debug + cic PASS; only design_setup/fc_setup(/compile.tcl) differ from Perforce |
| M4 real build + compile | done | build debug 1.1 min, trf 0.5 min (mipspd81); init_design debug 13 min, trf 3 min; compile stopped on RM's DFT Rule S19 exit (debug 5×, trf 1×) → new knob `continue_past_s19`; debug compile with knob ON: WARN, 10 min, WNS −0.89 / hold −1.48 / util 0.77 |
| M5 gates, resume, fault injection | in progress | gate_compile rendered + waiting (survived an Airflow restart); resume from compile reused build/init_design; live kill test on mipspd84: whole session killed, then clean relaunch |
| M6 full runs | pending | needs the gate approvals |
| M7 README | done | README.md |

Found and fixed during the build (beyond the plan):
* csh: a one-line `if (...) cmd >! file` creates the file **empty** → all such ifs are block form (test enforces it).
* The Rule-S19 fatal pattern must be anchored (`source -echo` echoes the Tcl source line too).
* An adversarial review (5 lenses, each finding verified by an independent skeptic) confirmed 37 issues; all
  fixed: launch-id based re-attach/adopt bookkeeping, session-wide liveness/kill (csh gives background
  children their own process group), atomic status files + sh session leader that writes rc if csh aborts,
  on_kill only kills when Airflow is healthy (and detached, since Airflow SIGKILLs the task 5 s after TERM),
  execution_timeout kills the job, soft timeout excludes license-queue time, finalize grades only a run dir
  this DAG run opened, lock re-taken on Clear, resume consumes old results and marks later steps stale,
  dry-run resume rejected, clock rounded once, error IDs from FC's message summary table, missing `Clk`
  group / open nets after route_opt FAIL, `module purge` + fc_shell path pin, umask 022, log-server ports.
