# TEAM_ROLLOUT — status 2026-10-14, how the build is sourced, and how the team can use raven_pd_flow

**Written:** 2026-10-14 02:10 PDT (checked live from mipscs82) · **Updated:** 2026-10-14 23:45 PDT (Airflow moved to mipspd86; Phase 1 done) · **Owner:** jsingh9 · **Code:** `/home/jsingh9.scratch/mywork/pd_flow_airflow/`
**Companion docs:** `HANDOFF.md` (state as of 10-13 01:50; §3 is superseded by Part A below) · `README.md` · `FLOW.md` · `SHARING.md`

> Part A = what is true right now (every number measured). Part B = how "the build" is sourced, in all senses.
> Part C = the rollout plan so colleagues can use the tool, with what is already done and what is left.
> Part D = the exact operator commands for the next hours.

---

## Part A — Status right now

> **Update 2026-10-14 23:45 PDT.** Done since the morning check: both mipscs Airflow instances stopped (mipscs82 23:20,
> mipscs83 23:21 while it answered ssh), **Airflow started on mipspd86** (tmux `pdflow`, UI 8081, fresh node-local DB,
> `doctor`: 17 PASS), password file chmod 600, Phase 1 items 1–9 implemented (69 tests pass) and committed. The
> corvi run has NOT been continued: that needs your Approve/Reject on the compile QoR (D.1, way 2 from mipspd86).


### A.1 The corvi compile FINISHED, graded WARN; the Airflow watching it lost its scheduler at that moment

| What | Measured |
|---|---|
| Run | `corvi_2Core_4VC_32K_20261006-232004`, attempt 4, mipspd81 session 1480441 |
| FC exit | `rc=0`, **41.3 h** (2026-10-12 02:46 → 2026-10-13 20:04 PDT), 85 GB RAM flat, 5.3 min license queue |
| Grading by Airflow (mipscs83, 20:04:58 PDT) | **compile: WARN** — S19 violated 38× (continued, knob ON); Error IDs: CMD-013 ×1, DWS-0302 ×6, ECOUI-108 ×288 ("No net connection for PI_RAM_GPI[*]"), LGL-083 ×6 146 822, SEL-005 ×1, SEL-006 ×1; clk echo 1.25, cores 16 |
| QoR (`pdflow.sh qor`) | **setup WNS −0.86 ns, TNS −18 496, NVP 103 222 · hold WNS −0.659 · util 0.78 · cell area 2.92 M** |
| On disk | `status/compile.rc`=0, `compile.consumed` = this launch, `qor/compile.json` written, markers `rm_fc/tmp/compile` + `place_opt` (20:04), 74 reports in `rpts/compile/` |
| run.json | `steps.compile.status = warn`; last history entry (10-12 run) has **no result** yet; `gates = {}` |

**What went wrong on the Airflow side:** 400 ms after the task reported success, mipscs83's **scheduler died** with
`sqlite3.OperationalError: database is locked` (in `_orphan_unreferenced_assets`), exactly the failure mode that killed
the mipscs82 instance on Oct 6. Its api-server and dag-processor are still alive (dag-processor re-parsed the DAG fine at
09:07 UTC today), the triggerer went quiet at 05:50 UTC 10-13. **With no scheduler, `gate_compile` was never scheduled**,
so there is no gate to approve and nothing moved on. mipscs83 was reachable for one minute at 02:04 PDT (load average
130 / 337 / 761, 409 users) and has been timing out on ssh since.

**Timing picture to review before continuing:** at a 1.25 ns clock the worst setup path misses by 0.86 ns with 103 k
violating endpoints. That is a design/constraints question for you, not something to wave through: it is exactly what
the compile gate exists for. The Approve/Reject decision is yours; both ways to continue are in Part D.

### A.2 The 10-13 updates did land

- Commits `4375423` (tech node + low-VT), `f334389` (RTL source selector), `b46d1d0` (HANDOFF) are on `main` and on `shared/main`.
- mipscs83's dag-processor re-parsed the new DAG successfully (1 DAG, 0 errors) at 09:54–09:58 UTC on 10-13 after a 6128 s stall during the freeze (the DAG was briefly marked stale, then re-activated), and again every minute since. The 16-field form is live there.
- The finished `fc_compile` task graded with the code it imported on 10-12 (module-level imports); the only `checks.py` change since is additive.

### A.3 Airflow instances

| Host | State at 23:45 PDT 10-14 | To do |
|---|---|---|
| **mipspd86** (**the Airflow host since 10-14 23:22**) | python3.11 3.11.13, tmux, Rocky 8.10, 64 cores, 995 GB RAM, `/var/tmp` 55 GB free, load ~25, 25 users, NTP-synced PDT; all NFS mounts present; ssh to mipspd81–85 works; `doctor` 17/17 PASS; DAG registered + unpaused | `tmux attach -t pdflow` there; tunnel `ssh -L 8081:127.0.0.1:8081 mipspd86`; log `airflow/standalone.mipspd86.log` |
| mipscs83 | **stopped 10-14 23:21** (its DB in `/var/tmp` keeps the old run history: run `manual__2026-10-12T09:46:25` is recorded there as `running`, fc_compile `success`) | nothing; the DB will age out of `/var/tmp` |
| mipscs82 | **stopped 10-14 23:20** | nothing |
| mipspd81 | python3.11 ✓, 128 cores, load ~38 | fallback Airflow host |
| mipspd82 | **no `/usr/bin/python3.11`** (idle, 128 cores) | not a candidate without IT |
| mipscs85 | python3.11 ✓ but load 40–56, 365 users | fallback only |

### A.4 Other facts found while checking

- `pdflow.sh list` shows `corvi_rvn_inova_20261001-012905` as `running?` although its `init_design.rc` is 143 (killed Oct 6). Cosmetic: the last history entry was never finalized. 2.8 GB.
- **Scratch is a shared 5 TB export at 95–98 %**: 133 GB free at 02:00, 258 GB at 23:18 (others' usage moves it). corvi runs are 5–8 GB each; precheck demands **150 GB free for size L**. Candidates to delete (both failed, nothing references them): `corvi_rvn_inova_20261001-012905` (2.8 GB), `corvi_2Core_4VC_32K_20261005-015151` (5.1 GB). Not deleted by me.
- The admin password file `airflow/home/simple_auth_manager_passwords.json.generated` was `-rw-rw-r--` in world-traversable directories since 10-05: anyone on the host could log in as admin and launch jobs **as you**. **Fixed 10-14:** chmod 600 done; `start.sh` now enforces it; `doctor` warns.
- `/var/tmp` carries a 30-day tmpfiles rule: an idle metadata DB can be deleted. Run history is per host anyway; `run.json` is the source of truth.
- The 10-13 `HANDOFF.md §3` sentence "trigger same tag + Start from = compile → the flow adopts the finished result" **was wrong** for a different host: precheck failed on `.pdflow.lock` (the other host's Airflow DB cannot be consulted), and after `rm` of the lock `prepare_workspace` marked the finished launch *consumed*, which forced a **full recompile**. **Fixed 10-14** (C.3 item 7): a finished, never-graded result of the `start_from` step is now adopted and graded; a step that *was* graded is re-run and precheck warns about it. For the current run compile was graded, so continue with Start from = `clock_opt_cts` (D.1).

---

## Part B — How "the build" is sourced

### B.1 The RTL source selector (trigger form field "RTL source", added 10-13)

| Value | What happens | Validation (`pdflow/buildsrc.py`) |
|---|---|---|
| **build** (default) | `build_rtl` runs `build.py` on the pd host via `scripts/10_build.csh` | none |
| **existing** | *Build path* = another run dir (or its `vbuild/<cfg>` / `py.Work`). The three `synthesis_{rtl,inc,pkg}.f` + `build.info` are **copied** into this run's `rm_fc_cad/vbuild/<cfg>/vbuild/py.Work/`; `PDFLOW_REUSED_FROM` records the source | `check_existing`: the three `.f` exist and are non-empty; `-tech` read from `build.info` must equal the chosen Technology node's `build_tech` (else *refused*: "a build is tied to its tech"); every `RTL_SOURCE_FILES` entry must exist |
| **filelist** | *Build path* = RM-style Tcl with `set RTL_SOURCE_FILES {...}` (+ `INC_DIRS`). Copied to `<run>/user_filelist.tcl`; the run copy of `<DESIGN>.FC.read_design.tcl` is rewritten to source it | `check_filelist`: absolute paths, all exist; **warning** if `RTL_PACKAGE_SOURCE_FILES` is used, because the RM override branch ignores it (list packages *first* inside `RTL_SOURCE_FILES`) |

List reusable builds:

```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh builds
# run (use as Build path, prefix /home/jsingh9.scratch/raven_local_ws/)  module        config                   -tech                                    date       how
# corvi_2Core_4VC_32K_20261006-232004                             corvi_module  2Core_4VC_32K            gf_22fdxp_9t_autopro150_9m_emram_stack   2026-10-07 built
# debug_2Core_4VC_32K_rvn_drive_20261006-230836                   debug_module  2Core_4VC_32K_rvn_drive  gf_22fdxp_9t_autopro150_9m_emram_stack   2026-10-06 built
# (+ corvi 20261005, corvi_rvn_inova 20261001, trf 20260930, debug 20260930 — all 9T eMRAM, all "built")
```

Give the form the **full path** (`/home/jsingh9.scratch/raven_local_ws/<run>`), not the bare name.

Pitfalls (all verified in code):
- A reused build's `.f` files contain absolute paths into the **source** run (`prpl/`, `include/`) and into the Perforce workspace. Delete the source run and FC breaks; sync Perforce and the RTL changes under a running job.
- `check_existing` does **not** check module, config, or that the build succeeded. `install_existing` copies into *this* run's `build_config` dir regardless. `builds` lists anything with a non-empty `synthesis_rtl.f`.
- `rtl_fixes` (the `RVN_EXCTYPE_SIZE` macro fix) target the new run's `include/`, which is empty in existing/filelist mode → logged `file-missing`. The fix only holds if the *source* run passed init_design (its header is already patched).
- A `Clear`/retry of `build_rtl` in filelist mode fails ("has no override branch"): the first install already rewrote the read script. The rewrite is not recorded in `patches/<module>.diff`.
- On resume you must re-select the same RTL source; the form default `build` is refused for a run made with existing/filelist (`context.py:163-164`). Pre-10-13 runs have no `build_source` and resolve to `build`.

### B.2 How the build environment is sourced on the pd host

`scripts/10_build.csh` (generated per run) sources `scripts/env_build.csh`, which is exactly:

```csh
unsetenv DISPLAY
setenv IMGSITE SC
source /mips/tools/freeware/Modules/latest/tcl/init/tcsh
setenv MIPS_HOME /home/jsingh9/Perforce/jsingh9_raven_ws2
source $MIPS_HOME/raven/source_env.csh $MIPS_HOME/raven >& <run>/logs/build.source_env.log
```

`source_env.csh` loads 31 modules (vcs/X-2025.06-1, python3/3.12.11, **fc/X-2025.06-SP2**, licserver/3.0) and sets
`MIPSARCHROOT`, `MIPS_CORE=raven`, `GCC_HOME`. The script aborts with rc 93 if `MIPSARCHROOT` is unset, 94 if `build.py`
is not executable, 95 if `rm_fc_cad/vbuild` is missing. **FC steps never source it** — they use `env_fc.csh`
(`module purge; module load fc/2022.12-SP6; limit stacksize unlimited; limit descriptors 65536`), because
`source_env.csh` would pull in the wrong FC. Then:

```csh
cd <run>/rm_fc_cad/vbuild
$MIPS_HOME/sys_base/tools/pyBuildSim/build.py ravenRTL -t $MIPS_HOME/raven/src/main/<module>/<top>.sv \
   --configName <cfg> --workDirectory <cfg> -bt Synthesis -tech gf_22fdxp_9t_autopro150_9m_emram_stack \
   -f /mips/proj/ravendrive/pd/libs/std_cells/globalfoundaries22nhsp/LATEST/hdl/lvt/1.00a/verilog/3.0/gf22napllogl20hdl116a.v \
   -f /mips/proj/ravendrive/pd/libs/std_cells/globalfoundaries22nhsp/LATEST/hdl/lvt/1.00a/verilog/std_primitives.v \
   < /dev/null >& <run>/logs/build.console.log
```

Output: `vbuild/<cfg>/build.log` (must end `Build Completed`; build.py can exit 0 on failure, so `checks.py` reads the
log), `vbuild/<cfg>/vbuild/py.Work/synthesis_{rtl,inc,pkg}.f`, `include/`, `prpl/`, `sim.Linux/`. Status files
`status/build.{pid,start,end,rc}` + `build.launch`/`build.consumed` written by the runner. Airflow launches it as
`ssh <host> /bin/sh -s` → `launch_build.sh` → `setsid env -i HOME USER LOGNAME SHELL=/bin/tcsh TERM=dumb LANG=C
PATH=/usr/local/bin:/usr/bin:/bin PDFLOW_LAUNCHED=1 /bin/sh -c '... /bin/csh -f 10_build.csh ...'`, so the job owns
its session and survives Airflow.

### B.3 Doing the build by hand

```csh
ssh mipspd81
csh -f /home/jsingh9.scratch/raven_local_ws/<run>/scripts/10_build.csh        # identical to what Airflow runs
#   watch: <run>/logs/build.console.log  or  <run>/rm_fc_cad/vbuild/<cfg>/build.log
# interactively instead (fresh shell; do NOT run FC from it afterwards):
source /home/jsingh9.scratch/raven_local_ws/<run>/scripts/env_build.csh
echo $MIPSARCHROOT
cd /home/jsingh9.scratch/raven_local_ws/<run>/rm_fc_cad/vbuild
<the build.py line from 10_build.csh:50>
```

A manual run rewrites `status/build.*` and the build output in place, but **not** `run.json`, and the PASS/FAIL checks
are not applied. To reproduce Airflow's clean environment exactly, prefix with `env -i HOME=$HOME USER=$USER LOGNAME=$USER
SHELL=/bin/tcsh TERM=dumb LANG=C PATH=/usr/local/bin:/usr/bin:/bin`.

Where the build settings live in `config/fixed.yaml`: `mips_home`/`rm_fc_cad_src`/`build_configs_dir` (:10-12),
`raven_env` + `build_env_extra: {IMGSITE: SC}` (:39-41), `build_py`/`build_project`/`build_target`/`build_tech`/
`build_extra_f` (:46-52), `tech_nodes` (:58-74, 5 entries pairing RM tech dir ↔ `-tech`), `modules.<m>.top_file /
default_build_config / filelist_override` (:78-109), `rtl_fixes` (:151-161).

---

## Part C — Rollout plan: make raven_pd_flow usable by the team

### C.1 Recommendation

**Each engineer runs their own Airflow instance, as themselves, from one shared read-only code install.**
Why: every job must run under the person's own account (ssh keys, FC licenses, scratch quota, Perforce workspace);
this Airflow has no per-user impersonation; a central service (SHARING "Way D") needs a service account, Postgres,
LDAP auth, an HTTPS proxy and IT. "Way A" (own clone) works today but has real blockers (C.3). Phase 2 removes
the per-person `pip install` and the venv copy. Phase 3 is the IT path if the team adopts it. Assumptions: internal
network only; the default Perforce source stays your world-readable workspace until a user sets `mips_home`.

### C.2 Phase 0 — operator actions, no code (Part D has the commands)

1. Decide the corvi gate (A.1) and continue the run (D.1). **Open: your call.**
2. ~~Stop the stale mipscs82 instance; `chmod 600` the password file.~~ **Done 10-14.**
3. ~~Move Airflow to a compute host~~ **Done 10-14: mipspd86** (fresh DB; `run.json` carries history).
4. Free scratch: the two failed corvi runs (7.9 GB) after a glance. Scratch was 95 % / 258 GB free at 23:18 (someone else freed space); precheck needs 150 GB for corvi.

### C.3 Phase 1 — fix what blocks colleagues (code + docs)

| # | Item | Status |
|---|---|---|
| 1 | `env.sh`: `AIRFLOW__API__HOST`/`BASE_URL` were set *after* `local.env` was sourced, so SHARING §7-B could never work → now `${VAR:-default}` | **done** |
| 2 | `env.sh`: per-user **instance dir** `PDFLOW_INSTANCE` (= `AIRFLOW_HOME`: airflow.cfg secrets, task logs, password, shutdown flag). Two Airflows from one clone shared `airflow/home` — the exact mipscs82/mipscs83 situation. Also `~/.pdflow/local.env` (wins over `airflow/local.env`; a user UI port there also re-derives the log ports so they never inherit the owner's 8795/8796) and `~/.pdflow/local.yaml` overlay (`config.py`) for users of a shared checkout | **done** |
| 3 | No `jsingh9` in code paths: `env.sh` DB-dir fallback → `$(id -un)`; DAG help text → `FIXED["runs_root"]`; `cli.py`/`airflow_ops.py` fallback URL → derived from `AIRFLOW_PORT` | **done** |
| 4 | `precheck` lock message names the holder's host and says that host's DB is not visible from here | **done** |
| 5 | `start.sh`: `chmod 600` the generated password file (immediately + a 10-min watcher for first start); print instance dir and code dir | **done** |
| 6 | `doctor`: code behind `shared/main` (warn → `git pull`); instance dir writable; password-file mode; log-server ports UI+1/UI+2 free (collision when another user's `uid % 20000` is within 2). 17 → 21 checks (log-port row only when Airflow is down) | **done** |
| 7 | **Adopt-on-resume**: `prepare_workspace` no longer `consume_finished()`s the `start_from` step when `run.json` says `running` and its launch was never graded; `run_or_reattach` then adopts and grades instead of recompiling (`runner.adoptable_result()`). Plus a **precheck WARN** when `start_from` would re-run an already graded step, naming the right next step. 3 unit tests | **done** |
| 8 | Tests portable: suite pins `PDFLOW_LOCAL=/dev/null`, derives paths from `fixed.yaml`'s `mips_home`, skips Perforce-dependent tests when unreadable, `REAL_BUILD` overridable via `PDFLOW_TEST_BUILD`. **69 passed** | **done** |
| 9 | Docs: HANDOFF §3/§4/§5 annotated; README §1 (mipspd86, per-user ports), §5 (kill rule + adopt row), §6/§10 + FLOW.md §8/§10 (`timeouts_h` → `stall_timeout_h` / `max_runtime_h`), §8 file list; SHARING: new way A′ (shared install + `pdflow-user-setup.sh`), doctor expectations, `standalone.<host>.log` note, way-B recipe, venv size, lock/re-run rows | **done** |
| 10 | Stand-in test: `doctor` + `pdflow-user-setup.sh` exercised with a throw-away `$HOME` (user port 27189, own instance dir, overlay picked up); a second person's real dry run from the shared install | **partly** — the second-person run is for a colleague to do |

`pytest` after items 1–9: **69 passed**; DAG imports cleanly and the mipspd86 dag-processor parses it with 0 errors;
`env.sh` verified for jsingh9 (8081/8795/8796), for a user with no `local.env` (derived port), and with
`~/.pdflow/local.env` overrides (`PDFLOW_INSTANCE`, `AIRFLOW__API__HOST`, log ports re-derived).

### C.4 Phase 2 — one shared read-only install (~1 day, after Phase 1)

Goal: colleagues need **no git clone, no internet `pip install`, no venv copy**.

1. **Location**: a group-readable project path, e.g. `/mips/proj/ravendrive/pd/FLOWS/pd_flow_airflow/` (dir is `imgtec` setgid, owned by kvs — confirm with them), or stay under `/home/jsingh9.scratch/mywork/...` (world-readable but on one person's 98 %-full scratch). Install = `git clone` of `shared/main` + `bash airflow/setup.sh` there; `git pull` by the maintainer only; tag releases. `setup.sh` needs PyPI + raw.githubusercontent.com (both reachable from mipscs82 today).
2. **`bin/pdflow-user-setup.sh`** — **done 10-14**: creates `~/.pdflow/` (mode 700) with `local.env` (`PDFLOW_INSTANCE=$HOME/.pdflow/home`, `VENV=<shared>/airflow/venv`, derived ports, node-local `AF_DB_DIR`), `local.yaml` (commented `mips_home`, `runs_root`, `fc_hosts`), prints the start / tunnel / UI / CLI lines and runs `doctor`. `env.sh`/`config.py` honour both files. Today the "shared install" *is* the owner's checkout `/home/jsingh9.scratch/mywork/pd_flow_airflow` (world-readable); moving it to a project path is item 1.
3. Entry point: `<shared>/bin/pdflow.sh` unchanged (sources `env.sh`); optionally a modulefile `pdflow/1.0`.
4. DB stays node-local per user (`/var/tmp/<user>_pdflow_airflow_db`); document that run history is per host.
5. `doctor`: shared venv readable, instance dir writable (item 6).

### C.5 Phase 3 — central team service (later, needs IT; SHARING "Way D")

Service account with FC license + ssh keys to the pd hosts; Postgres instead of SQLite; `apache-airflow-providers-fab`
with LDAP; HTTPS reverse proxy; scheduler on a non-login server; a per-run "requested by" field; pools sized for the
team (today `pd_fc=2`, `pd_build=2`, `parallelism=8`, `max_active_runs=4`); `runs_root` on a project volume with quota.

### C.6 What is left

- Your Approve/Reject on the corvi compile QoR and the trigger that continues it (D.1).
- A colleague to run way A′ end to end (`pdflow-user-setup.sh` → `start.sh` → debug dry run → `report`).
- Phase 2 item 1 (a project path for the shared install) and Phase 3 when/if the team wants a central service.

### C.7 Verification (for Phase 1/2 completion)

- `bash -c 'source airflow/env.sh; $VENV/bin/python -m pytest -q tests'` → all pass, from the main clone **and** from a fresh clone whose `local.yaml` sets `mips_home`.
- Fresh clone as a stand-in on mipscs85: `doctor` before start → 16 checks, 2 WARN, 0 FAIL; after `start.sh` → all PASS; debug_module dry run → 13 green boxes; `pdflow.sh report <tag>` → tar.gz.
- Item 7: unit test + a debug_module run where Airflow is stopped mid-compile and restarted on another host; trigger same tag with Start from = compile → log shows `ADOPTED`, no second `compile.log.<ts>`.
- Phase 2: a second user runs `pdflow-user-setup.sh`, starts on their port from the shared venv, completes the debug dry run; nothing of theirs is written under the shared install.
- Security: password file 0600 after start; API bound to `127.0.0.1` only (`ss -ltnp`).

---

## Part D — Operator runbook for the next hours

### D.1 Continue the corvi run — DONE 2026-10-15 03:35 PDT
Triggered from mipscs85 (`af.sh dags trigger`): same tag, Start from = clock_opt_cts, **host mipspd84** (mipspd81 had only
102 GB free RAM; precheck demands 200 GB for corvi), S19 knob on, gates on. compile REUSED (warn), gate_compile
auto-approved (past start_from), `fc_clock_opt_cts` launched on mipspd84 session 1432873. Next human stop: **gate_clock_opt**
after clock_opt_opto (UI → Required Actions, or `pdflow.sh gates` / `approve`). The old lock was backed up as
`.pdflow.lock.mipscs83-*.bak` in the run dir. The original recipe, for reference:


```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh qor corvi_2Core_4VC_32K_20261006-232004   # decide: WNS -0.86, hold -0.659
rm /home/jsingh9.scratch/raven_local_ws/corvi_2Core_4VC_32K_20261006-232004/.pdflow.lock     # held by mipscs83's dead run
ssh -f -N -L 8081:127.0.0.1:8081 mipscs85        # UI http://127.0.0.1:8081 -> raven_pd_flow -> Trigger:
#   Module corvi_module · Run tag corvi_2Core_4VC_32K_20261006-232004 · Start from = clock_opt_cts · pd host mipspd81
#   Max cores 16 · Clock blank (1.25 inherited) · Continue past S19 ON · gates on
```
Compile is **reused** (status warn, marker + nlib present), nothing is re-run; `gate_compile` auto-approves because it
is before `start_from` (the review is the `qor` line above), `gate_clock_opt` waits for you as usual.
If you choose Start from = **compile** instead, precheck now WARNs "RE-RUNS that step (41.3 h last time); to continue
after it use start_from=clock_opt_cts" — it would recompile, because the result was already graded.

### D.2 Housekeeping
```csh
# optional, 7.9 GB: both FAIL at init_design (RTL macro, fixed since); nothing references them
rm -rf /home/jsingh9.scratch/raven_local_ws/corvi_rvn_inova_20261001-012905 /home/jsingh9.scratch/raven_local_ws/corvi_2Core_4VC_32K_20261005-015151
```

### D.3 The Airflow host is mipscs85 since 10-15 (keep-alive + Windows access, no gateway)
```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/watchdog.sh install    # on mipscs85: cron restarts Airflow if down/unhealthy
# Windows PowerShell (direct):  ssh -N -L 8081:127.0.0.1:8081 jsingh9@mipscs85.mipstec.com   -> http://localhost:8081
# (airflow/ui-tunnel.sh relays via a Linux gateway only if direct ssh to mipscs85 is blocked from your PC)
```
Airflow runs under `nice 10` + low I/O priority with 6 executor workers, 1 API worker and a 2-min DAG parse (env.sh): 18 processes, ~2.0 GB RSS, ~2 % of one core idle.
```csh
ssh mipscs85; tmux attach -t pdflow                      # Ctrl-b d to detach; bash airflow/stop.sh to stop
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/airflow/status.sh      # on mipscs85
ssh -f -N -L 8081:127.0.0.1:8081 mipscs85                # from your desktop; admin / password in airflow/home/...generated
```
The old run history stays in mipscs83's `/var/tmp/jsingh9_pdflow_airflow_db` (ages out after 30 idle days); every run
dir has its `run.json`, and `pdflow.sh list|status|qor` read those.

### D.4 Code state
All Phase 1 changes are committed on `main` and pushed to `shared/main` (see `git log`). The mipspd86 dag-processor
re-parses the DAG folder every 60 s, so it already runs the new code; tasks import `pdflow` fresh when they start.
