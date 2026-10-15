# HANDOFF — state of the Raven PD-flow Airflow project (for a new session)

**Written:** 2026-10-13 01:50 PDT (§3/§5 annotated 2026-10-14) · **Owner:** jsingh9 · **Code:** `/home/jsingh9.scratch/mywork/pd_flow_airflow/`
**Clone:** `/home/jsingh9.scratch/git/pd_flow_airflow.git` (bare; `git push shared main` publishes)
**Docs:** `README.md` (operator guide) · `FLOW.md` (how the DAG works, dark-mode charts) · `SHARING.md` (how colleagues run their own copy) · `PLAN.md` (original plan + status) · `PROJECT_STATEMENT.md` · **`TEAM_ROLLOUT.md` (10-14 status, build sourcing, team rollout)**

> How to read this: §1 says what the thing is, §2 what is proven to work, §3 what is running / pending
> **right now**, §4 the known problems and their status, §5 how to continue. Every number was measured.

---

## 1. What was built

An Apache Airflow 3.3.2 DAG, `raven_pd_flow`, that takes one Raven module from RTL build through Synopsys
Fusion Compiler (RM flow) to **route_opt**, with two human review gates. Plain-Python logic in `pdflow/`,
thin DAG in `airflow/dags/raven_pd_flow_dag.py`, predefined values in `config/fixed.yaml`.

```
resolve_inputs → precheck → prepare_workspace → build_rtl → fc_init_design → fc_compile → [gate_compile]
  → fc_clock_opt_cts → fc_clock_opt_opto → [gate_clock_opt] → fc_route_auto → fc_route_opt → finalize
```

Key design facts (details in FLOW.md):
* Each run = `/home/<user>.scratch/raven_local_ws/<run_tag>/` with its **own copy** of `$MIPS_HOME/rm_fc_cad`
  (`/home/jsingh9/Perforce/jsingh9_raven_ws2`, never modified). `summary.md`, `run.json`, `patches/`,
  `scripts/`, `status/`, `qor/`, FC logs in `rm_fc_cad/<module>/rm_fc/logs/`, reports in `.../rpts/<step>/`.
* Tool steps run **detached on a pd host** (mipspd81–85) via `ssh host /bin/sh -s` + generated csh scripts
  (`scripts/NN_<step>.csh`, re-runnable by hand). Airflow only reads NFS status files + tails the log.
  Jobs survive Airflow restarts; a retried/cleared task **re-attaches** or **adopts** the result.
* `fc_shell` is called directly (not `make`: its `| tee` hides failures). FC steps get `limit stacksize unlimited`,
  `descriptors 65536`, `module purge; module load fc/2022.12-SP6`, license queueing up to 12 h.
* Pass/fail is not the exit code: done-marker freshness, "Thank you for using Fusion Compiler", fatal patterns +
  ELAB/VER/LNK/NDM in FC's message summary table, echoed `Clk_period`/`-max_cores`, reports present, `Clk` path
  group period, open nets after route_opt. Other `Error:` IDs = warnings (listed in the gate).
* Steps are stopped only when **hung** (`stall_timeout_h: 6` with no log growth) or past a backstop
  (`max_runtime_h`: S 48 / M 120 / L 240). License wait is not counted. No wall-clock cap otherwise.
* The FC job is killed only on a user **Mark failed/success**; Airflow-internal stops never kill it.
* Retries: setup tasks + finalize 2×; tool steps 3× but only for ssh/host trouble (real flow failures =
  `AirflowFailException`, no retry); gates never retry.

### Trigger form (16 params)

| Field | Values / default | Notes |
|---|---|---|
| pd host | mipspd81..85 (required) | precheck prints load/RAM of all 5; **fails** if RAM < module need (L 200 GB) |
| Module | corvi / cic / debug / trf (required) | sets DESIGN_NAME, top RTL, default config + clock |
| Build config | module default | `-c`/`SYN_CONFIG`; 41 dirs in `raven/src/config` |
| **RTL source** | build (default) / existing / filelist | existing = reuse another run's `synthesis_*.f` (its `-tech` must match the tech node, else refused); filelist = RM-style Tcl. `pdflow.sh builds` lists reusable builds. (added 10-13) |
| **Build path** | blank | for existing/filelist |
| **Technology node** | 9T eMRAM stack (default) + 4 others | RM tech dir **and** `build.py -tech` together; refused if the module lacks it; fixed per run. (added 10-13) |
| Clock period (ns) | module default | patches `set Clk_period` |
| Max cores | 16 | patches `set_host_options -max_cores` (fc_setup.tcl is the effective one) |
| **Max low-VT cells (%)** | 10 | patches `set_multi_vth_constraint -low_vt_percentage` (added 10-13) |
| Continue past DFT Rule S19 | **off** | the RM `exit`s on S19; ON = warn + continue. **Not remembered between triggers.** |
| Start from / Stop after | build / route_opt | resume = same Run tag + same Module + Start from = step to redo |
| Run tag | auto | required for resume; leading/trailing spaces tolerated |
| gates | on / on | |
| Dry run | off | new runs only |

### CLI (`bash bin/pdflow.sh …`)
`list [--live]` · `status <tag>` · `qor <tag>` · `kill <tag>` · `gates` · `review <tag>` · `approve/reject <tag> -m ".."`
· `builds` · `doctor` (17 checks) · `report <tag>` (tar.gz for bug reports)

### Config split for other users
`config/fixed.yaml` (shared) + `config/local.yaml` (per user, git-ignored) · `airflow/env.sh` + `airflow/local.env`
(jsingh9's keeps UI 8081, log ports 8795/8796; others get `20000 + uid % 20000`). `${USER}` expands.

---

## 2. What is proven (real runs)

| Run | Result |
|---|---|
| debug_module `debug_2Core_4VC_32K_rvn_drive_20260930-001616` | full flow **PASS** (route_opt WNS −0.389, hold −0.141, 399 DRC, 0 open nets) |
| debug_module `debug_2Core_4VC_32K_rvn_drive_20261006-230836` | full flow **PASS** unattended (gates off), 1 h 20 min FC; init_design was retried after an Airflow restart and **ADOPTED** the finished job |
| trf_module | build + init_design ok; compile stopped at S19 (knob was off) |
| cic_module | dry run only |
| corvi_module | see §3 |
| Stand-in tester | fresh clone on its own port/DB ran a dry run PASS alongside the main instance; found+fixed 2 bugs |
| Adversarial code review (10 agents) | 37 confirmed issues, all fixed (re-attach bookkeeping, session-wide kill, atomic status files, finalize grading, locks, …) |

Unit tests: `bash -c 'source airflow/env.sh; airflow/venv/bin/python -m pytest -q tests'` → **64 passed**.

---

## 3. State RIGHT NOW (2026-10-13 01:50) — SUPERSEDED, see `TEAM_ROLLOUT.md` Part A (2026-10-14)

> 10-14: the corvi compile finished (rc 0, 41.3 h, graded WARN; setup WNS −0.86 ns); mipscs83's scheduler died the same
> second (SQLite "database is locked"), so no `gate_compile` was created. Both mipscs instances were stopped on 10-14 and
> **Airflow now runs on mipspd86** (tmux `pdflow`, UI 8081, fresh DB). The adopt sentence below was wrong for a different
> host and is fixed in code (see §4); the current run continues with Start from = `clock_opt_cts` (compile is reused).


* **Airflow runs on mipscs83** (tmux `pdflow`, UI 8081) — but **mipscs83 has been unreachable over ssh since
  ~23:30 on 10-12** (login servers mipscs82/83 freeze under load; it happened 4× in 2 weeks). Its Airflow was
  still alive (log written 23:27, heartbeats every 5 min). An **old stale Airflow copy still runs on
  mipscs82** (Oct 6) — harmless, stop it with `ssh mipscs82 bash .../airflow/stop.sh` when reachable.
  mipscs85 (this session's host) has python3.11 and could host Airflow; **mipspd81 would be the stable home**.
* **Live job:** corvi compile, run `corvi_2Core_4VC_32K_20261006-232004`, attempt 4, on **mipspd81** session
  1480441, started 10-12 02:46, ~23 h in, memory flat at 85 GB, in `initial_opto / Optimization (3)` (this
  sub-step took 12 h last time). Knob **S19 ON** this time, max_cores 16, tech 9T eMRAM, config 2Core_4VC_32K.
  Expect it to end ~10-13 morning/afternoon; then **gate_compile waits for Approve**.
  ~~If mipscs83's Airflow is dead when it ends: start Airflow elsewhere, trigger same tag + Start from = compile
  → the flow adopts the finished result (no recompile).~~ **Wrong before 10-14:** on another host precheck failed on
  `.pdflow.lock`, and after `rm` of the lock `prepare_workspace` marked the finished launch *consumed* → full recompile.
  Since 10-14 `prepare_workspace` keeps an unconsumed, never-graded result of the `start_from` step (`adoptable_result`)
  and `fc_<step>` adopts + grades it; a step that was already graded is re-run (precheck now warns, with the right
  `start_from` to continue instead).
* Corvi history: attempt 1 (mipspd83) **segfault rc 139** after 3.4 h (8 MB stack, swap full, host overloaded)
  → fixed by unlimited stack + RAM precheck; attempt 2 (mipspd84) killed by the old 24 h cap while healthy →
  cap removed (stall-based now); attempt 3 (mipspd81) 28 h, FC exit 0 but **stopped at DFT Rule S19 (38
  violations)** because the knob was off.
* The three form additions of 10-13 (RTL source, tech node, low-VT %) are committed/pushed; the running Airflow
  picks them up at its next DAG parse (if reachable). They do not affect the live corvi task.

---

## 4. Known issues / decisions

| Item | Status |
|---|---|
| Login servers freeze (mipscs82/83) → UI unreachable, scheduler dies (SQLite "database is locked" ×2), DAG "stale" (no Trigger button) | mitigated: 24 h task tokens, 200 missed heartbeats tolerated, 5 min DAG parse timeout, stale threshold 30 min, `doctor` flags paused/stale DAG, `start.sh` unpauses; **10-14: Airflow moved to mipspd86** (compute host, python3.11, 64 cores, 1 TB RAM, no login-host freezes) |
| Resume on another host could not adopt a finished-but-ungraded step (it recompiled) | fixed 10-14: `runner.adoptable_result` + `prepare_workspace`; precheck warns when `start_from` would re-run a graded step |
| Admin password file world-readable; two Airflows from one clone shared `airflow/home` | fixed 10-14: `start.sh` chmods it 600; `PDFLOW_INSTANCE` gives each instance its own dir; `doctor` checks both |
| corvi RTL: `` `RVN_EXCTYPE_SIZE `` macro uses `$unsigned(...)` as a cast width → FC VER-294/260, 57 unbound modules | auto-fixed in the run's build output (`rtl_fixes` in fixed.yaml, applied at init_design, logged as "rtl fix … applied"). **Report upstream to RTL owners.** |
| DFT Rule S19 (nonscan cell disturb): debug 5×, trf 1×, corvi 38× (flops in `i_rvn_biu/.../biu_mmr_regblk`) | knob `continue_past_s19`; the RM's exit is deliberate — a DFT owner should look at the 38 async set/reset flops |
| corvi compile: 6× `DWS-0302 Datapath generation failed` (logged as warning) | not investigated |
| corvi `initial_opto / Optimization (3)` takes 7–12 h vs 33 min for Optimization (2) | unexplained; watch |
| Max cores > 16 may need extra FC licenses | not verified |
| `custom filelist`: RM override branch ignores `RTL_PACKAGE_SOURCE_FILES` → packages must be first in `RTL_SOURCE_FILES` | documented, precheck warns |
| Perforce `p4` CLI not installed → no sync step; runs copy whatever is in the workspace | by design |
| Core dumps go to systemd-journal (root only) → segfault root cause can't be read without IT | open |

---

## 5. How to continue

```csh
# where things stand
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh list --live
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh status corvi_2Core_4VC_32K_20261006-232004
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh doctor
# Airflow on a host (inside tmux); DB is node-local (/var/tmp/<user>_pdflow_airflow_db), run history per host
tmux new -s pdflow 'bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/start.sh'
ssh -f -N -L 8081:127.0.0.1:8081 <airflow-host>      # then http://127.0.0.1:8081, user admin,
#   password: airflow/home/simple_auth_manager_passwords.json.generated
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh gates   # / review / approve <tag>
```

Next steps, in order (10-14 update: 2 is done — Airflow is on mipspd86, the mipscs82/83 copies are stopped):
1. corvi compile is done (WARN, setup WNS −0.86 ns). Review `pdflow.sh qor corvi_2Core_4VC_32K_20261006-232004`; to
   continue: `rm <run>/.pdflow.lock` (held by mipscs83's dead run), trigger same tag, Module corvi_module, **Start from =
   clock_opt_cts**, S19 knob ON → clock_opt_cts → opto → gate → route_auto → route_opt. First full corvi run.
2. ~~Decide the Airflow host~~ done: mipspd86 (`tmux attach -t pdflow` there; tunnel `ssh -L 8081:127.0.0.1:8081 mipspd86`).
3. Set `max_runtime_h` / sizes from real corvi runtimes; consider `max_route_opt_drcs`.
4. Report the RTL macro and the S19 flops upstream.
5. Hand colleagues `SHARING.md` (each runs their own copy; `pdflow.sh report <tag>` for bug reports).

Conventions used throughout: never modify Perforce; every change lives in the run copy and is recorded in
`patches/<module>.diff`; csh scripts use block-form `if` (a one-line `if (...) cmd >! f` leaves `f` empty);
commit with `git commit` in the project, publish with `git push shared main`.
