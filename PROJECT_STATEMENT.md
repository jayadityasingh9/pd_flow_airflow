# FC-Flow AI Automation Tool — Project Statement, Objectives & Tools

| | |
|---|---|
| **Project** | An AI-assisted tool that automates the Synopsys Fusion Compiler (FC) physical-design flow for Raven modules, from RTL to timing/equivalence sign-off |
| **Author** | jsingh9 |
| **Date** | 2026-10-05 (status as of 00:45 PDT) |
| **Status** | In development. The orchestration core is built and has taken one module (debug) from RTL through route_opt in real runs. AI: one standalone report-summarizer prototype (pico sample flow only); nothing in the Raven flow yet |
| **Sponsor / reviewer** | To be named |
| **Related docs** | [`README.md`](README.md) (operator guide) · [`PLAN.md`](PLAN.md) (design and status) · `vbuild_ui_problem_statement.md` (RTL-build problem statement; in the author's `mywork/docs/`, not yet in a repo) |

> **How to read this document.** Every number was measured from the workspace, the run
> directories or the code; Appendix A gives the commands. Sizes that grow over time are as of
> 2026-10-05. Features are labelled **EXISTS** (built and run) or **PLANNED**.
>
> **Paths:** `PF` = `/home/jsingh9.scratch/mywork/pd_flow_airflow` (this folder) ·
> `DAG` = `PF/airflow/dags/raven_pd_flow_dag.py` · `RW` = `/home/jsingh9.scratch/raven_local_ws`
> (run directories) · `MW` = `/home/jsingh9.scratch/mywork` · `IN` = `/home/jsingh9.scratch/indus`
> (team repo `github.com/MIPS/indus`) · `P4` = `/home/jsingh9/Perforce/jsingh9_raven_ws2/rm_fc_cad`
> (read only) · `CV` = `P4/corvi_module` · `MK` = `CV/scripts_block/rm_setup/Makefile` ·
> `DS` = `CV/block_files/GF_22FDXP_9T_AutoPro150_9M_eMRAM_stack/design_setup.tcl` ·
> `S` = `CV/scripts_block/rm_fc_scripts`.
>
> **Run tags:** `debug…001616` = `debug_2Core_4VC_32K_rvn_drive_20260930-001616`,
> `trf…004850` = `trf_2Core_4VC_32K_rvn_drive_20260930-004850`,
> `corvi…012905` = `corvi_rvn_inova_20261001-012905`. FC logs of a run are in
> `RW/<tag>/rm_fc_cad/<module>/rm_fc/logs/`.

---

## 0. Short form (paste-ready)

**(a) Project Statement.**
Taking one Raven block through the Synopsys Fusion Compiler (FC) physical-design flow takes
about 44 manual actions today, with 3–4 files hand-edited just to reach routing. The
flow can report success when it has failed: in one measured run, a 1.4-hour step exited
normally although its log held RTL syntax errors at line 1,818 of 71,431. Reviews wait for a
person with no alert, and no run history is kept. This project builds a tool that runs the
whole flow from one request. Its deterministic orchestrator is built and has taken one block
from RTL through routing in real runs, grading every step against independent evidence. A
planned AI layer will explain failures and results; approvals stay with named engineers.

**(b) Objective Statement.**
Deliver a tool that any Raven PD engineer can use to take any of the 4 Raven blocks from RTL
through timing (PrimeTime) and equivalence (Formality) sign-off with one request and no
hand-edited files. Success means: 19 of the 23 Reference-Methodology flow targets automated
(7 wired and 6 run for real today; the finishing steps once CAD supplies the missing
collateral); zero failed steps reported as passed; each failure flagged within 5 minutes with
an AI-drafted, cited probable cause that engineers confirm on at least 80% of 20 or more
labelled cases; reviewers alerted within 1 minute, with every gate decision recorded against
a named engineer; and the Perforce source never modified.

---

## Table of contents

1. [Background](#1-background) · 2. [Problem Statement](#2-problem-statement) ·
3. [Objective Statement](#3-objective-statement) · 4. [Scope](#4-scope) ·
5. [Proposed Solution](#5-proposed-solution) · 6. [Tools & Technologies](#6-tools--technologies) ·
7. [Current Progress](#7-current-progress) · 8. [Roadmap](#8-roadmap) ·
9. [Success Metrics / KPIs](#9-success-metrics--kpis) · 10. [Risks & Mitigations](#10-risks--mitigations) ·
11. [Deliverables](#11-deliverables) · [Appendix A](#appendix-a--basis-of-measurement) ·
[Appendix B](#appendix-b--adjacent-tools-and-prior-work)

---

## 1. Background

**The FC flow.** Physical design turns a block's RTL into a placed, routed and verified layout.
Raven blocks use the Synopsys **Reference Methodology (RM)** scripts for Fusion Compiler, kept
per module in `P4/<module>/` and driven by a Makefile with **30 targets** (`MK`): **23** flow
steps and 7 housekeeping targets (console, config, setup, all, backup, clean, clean_all).

```
RTL build (vbuild/pyBuildSim) → sanity → init_design → compile (+place_opt, unified flow)
→ clock_opt_cts → clock_opt_opto → route_auto → route_opt → chip_finish → icv_in_design
→ write_data → StarRC → PrimeTime (DMSA) → [PT-ECO → timing_eco] → Formality (r2g / g2g)
```

Inputs are RTL filelists from `vbuild` (`CV/block_files/common/corvi_module.FC.read_design.tcl:9-21`);
`write_data` outputs netlists, SPEF, DEF, GDS and OASIS (`S/write_data.tcl:58-86`, corvi copy).

**Why it is long and expensive.**
- **Runtime:** corvi `init_design` alone took **1.36 h**, 9.6 min of it in the license queue. The 6 debug FC steps from init_design to route_opt took **5,804 s ≈ 1.6 h** (`run.json` `steps.*`).
- **Hardware and licenses:** mipspd81/82 have 128 cores, mipspd83–85 have 32 (`PF/PLAN.md:60-66`); Synopsys licenses are queued (`PF/config/fixed.yaml:27-30`).
- **Volume:** one step log is **71,431 lines / 5.39 MB** (corvi `init_design.log`); the debug run to route_opt left **240 report files**.

**Raven context.** 4 modules have RM trees: `corvi_module` (size L), `cic_module` (M), `debug_module`
(S) and `trf_module` (S) (`fixed.yaml:52-84`). All use GF 22FDX and RM R-2020.09-SP6 (`MK:4`), with
**41** build configs. Compared with corvi (`diff -rq`), route_opt.tcl and the Makefile differ in all
3 other modules; cic also differs in init_design.tcl, init_design.from_rtl.tcl, read_rtl.tcl and
fc_setup.tcl; debug and trf in write_data.tcl and `star_cmd`. compile.tcl is identical in all 4.

---

## 2. Problem Statement

### 2.1 Current state (as-is)

Running one module by hand (sources: `CV/run.csh:1-28`, `PF/PLAN.md:7-13`, `P4/block/README:9-29`):

| Phase | What the engineer does | Actions | Files hand-edited |
|---|---|---|---|
| A. Setup | ssh to 1 of 5 hosts, `source_env.csh`, 3 × `cd`, `cp -rfp` the RM kit, `chmod`, edit `example.build` (a 447-character command line), build, read `build.log`, edit `Clk_period`, edit `max_cores` (2 files), edit `run.csh`, and the 4 `module switch` lines (scripted in `CV/run.csh:9-12`, but needed in the shell for the hand-typed `make` calls in B) | ≈ 18 | `example.build`, `design_setup.tcl`, `fc_setup.tcl`, `run.csh` |
| B. Implementation | 6 `make` calls (init_design … route_opt), each with 3 variables typed by hand, plus 6 log/report checks | 12 | — (18 values typed) |
| C. Finishing | chip_finish, icv_in_design, write_data, plus 3 checks; missing filler script, ICV runsets and GDS map supplied first | 6 | +1–3 knobs |
| D. Sign-off | `make starrc`, `pt`, `fm_r2g`, `fm_g2g` (with `cd`s); for cic/debug/trf also create `rm_starrc/` and `rm_pt/`, and fix `pt_setup.tcl:114` for cic and trf | 6 | `pt_setup.tcl` |
| E. Review | open `pt_qor.rpt`, then `pt_shell` → `restore_session` → `report_timing` | ≥ 2 | — |

**Totals:** about **30 actions to reach route_opt** and **44 to reach sign-off**; **3–4 hand-edited
files** for implementation and **≥ 6** for sign-off. Every result is read from raw logs.

### 2.2 Identified problems

**P1 — Too many manual, error-prone actions.**
- 18 hand-typed `make` values (§2.1), and wrong defaults in **3 of 4** module Makefiles: cic TECH_DIR `GF_22FDXP_6P75T_116A` does not exist in its `block_files` and DESIGN_NAME is `corvi_module` (`P4/cic_module/scripts_block/rm_setup/Makefile:21,29`); debug TECH_DIR does not exist (`P4/debug_module/…/Makefile:21`); trf SYN_CONFIG `rvn_bumblebee_trf` is not one of the 41 configs (`P4/trf_module/…/Makefile:25`).
- Copy-paste drift: cic and trf `pt_setup.tcl:114` point at `corvi_module.SPEF.CWorst_m40c`.

**P2 — A large knob space and several tool versions, mostly uncontrolled.**
- `DS` has **271** upper-case `set` knobs: **113** empty and **25** user hook slots (11 filled). `fc_setup.tcl` has **29** `set` lines.
- **3 FC versions** are in play: `fc/2022.12-SP6` (`CV/run.csh:9`), `fc/X-2025.06-SP2` from Raven `source_env` (`PF/PLAN.md:108`), `fc/V-2023.12-SP5-3` in the pico sample flow (`IN/pico_fc_flow/rm_fc/README.md:12`).

**P3 — The flow reports success when it has failed.**
- **23** Makefile recipes pipe through `| tee` and **0** use `pipefail` (`MK`), so `make` sees the exit code of `tee`.
- `sh_continue_on_error true` is in every design_setup (`DS:594`). Of **148** `RM-error` lines in the 32 RM scripts, only **28** exit or return within 3 lines. The only success signal is a marker file written on each step's last line.
- **Measured:** corvi init_design exited **rc 0 and wrote its marker** despite **152 `^Error` lines**, including 8 VER-294 RTL syntax errors (§7.2 row 6; FC's own summary says "128638 errors", `init_design.log:71424`). trf compile exited **rc 0** after an S19 stop (§7.2 row 4); under `make`, the missing marker would make the next target silently re-run compile (`MK:63,70`; `PF/README.md:150`). `build.py` calls `sys.exit()` with no code after an error (`sys_base/tools/pyBuildSim/build.py:53-55`).

**P4 — Failures are found late, inside huge noisy logs.**
- corvi ran **1.36 h** before anyone knew it had failed; the decisive error is at line **1,818 of 71,431** (2.5%).
- Accepted steps report errors too: debug init_design has 55 errors and 29,382 warnings (`RW/debug…001616/…/logs/init_design.log:12420`); debug route_opt, graded WARN, has 2 (`route_opt.log:16045`). "Has errors" cannot be the test.
- A wrong SDC clock port is found only inside init_design (13 min lost on debug, 1.36 h on corvi).

**P5 — Hard stops and missing collateral block the back half of the flow.**
- **DFT Rule S19** hard-exits compile in all 4 modules (`S/compile.tcl:423-425`). It stopped both real compile runs that used the RM default (debug run 2, trf); DFT DRC reported 5 S19 violations on debug and 1 on trf (`PF/README.md:65`; `rpts/compile/dft_drc_full.rpt`).
- chip_finish sources a filler script that exists nowhere in `P4` (`S/chip_finish.tcl:70`). ICV runsets, GDS layer map, eco_opt PT path and RedHawk tech file are empty (`DS:387,415,428,511,457`); there is no SAIF (`DS:225`) and no LVS step in the RM.
- `make all` ends at write_data, which depends on route_opt, so chip_finish and icv_in_design never run (`MK:89`; `DS:615`).

**P6 — Review is manual and the human gate is the bottleneck** (debug run 3, the only run to reach both gates; n = 1, illustrative).
- Compile ended at 01:21:22 and the next step started **9,197 s (2 h 33 min)** later (`status/compile.end`, `status/clock_opt_cts.start`): **84%** of the 10,984 s from compile start to clock_opt_opto end.
- The clock_opt gate went **unanswered for 72 h** and the run failed (`RW/runs_index.tsv:8`). There are no notifications (`PF/PLAN.md:278`).
- The gate shows numbers without interpretation, e.g. **91.2% utilization** after clock_opt_opto (`run.json` `steps.clock_opt_opto.facts.qor`).

**P7 — No history, traceability or shared access.**
- A manual run leaves no record. The orchestrator has one `admin` account and a UI bound to 127.0.0.1 behind an ssh tunnel (`PF/airflow/env.sh:58-59`). Its node-local SQLite history was lost when Airflow moved from mipscs82 to mipscs83 (`env.sh:49-51`; `PF/README.md:25`).
- **Gate history is overwritten:** run 4's resume replaced both `run.json.gates` records with "auto-approved", and finalize regenerated `summary.md`. The record that `admin` approved the compile gate at 03:54:36 (read from `summary.md:28` on 2026-10-05 at 00:30) is gone; only `runs_index.tsv:8` and `run.json` `history[2]` still show the clock_opt timeout.
- `MW/summarize_reports.py` is wired only into the pico sample flow (§7.3).

**P8 — License waits and disk limits are not handled by the manual flow.**
- The Makefile's `SNPSLMD_QUEUE` / `SNPS_MAX_*` exports are no-ops (`MK:9-11`; `PF/PLAN.md:107`). Real runs did queue: 9.6 min (corvi init_design), 1.0 min (debug clock_opt_cts) and 6.3 min (debug route_auto) (`run.json` `facts.license_queue_min`).
- Scratch is **89% full** (`df -h`), and each run adds one nlib (`PF/PLAN.md:268`).

### 2.3 Formal problem statement

> Running a Raven module through the Fusion Compiler flow today takes about 30 manual actions to
> reach route_opt and 44 to reach timing/equivalence sign-off, with 3–4 hand-edited files for
> implementation and at least 6 for sign-off. The flow has no trustworthy success signal: exit
> codes are masked by `tee`, Tcl errors are suppressed, and a step that exits 0 and writes its
> marker can still contain fatal RTL errors. Failures are found late, after hours of compute, in
> logs of up to 71,431 lines. QoR review is manual, with no interpretation and no notification:
> in the only run to reach both gates, one waited 2 h 33 min and the other expired unanswered
> after 72 h. Nothing reliably records run or gate history, and the existing automation is
> single-user. **The problem to be solved is the lack of a trustworthy, single-trigger,
> reviewable path through the FC flow.**

---

## 3. Objective Statement

### 3.1 Primary objective

**Deliver a tool that any Raven PD engineer can use to take any of the 4 Raven blocks from RTL
through timing (PrimeTime) and equivalence (Formality) sign-off with one request and no
hand-edited files. Success means: 19 of the 23 Reference-Methodology flow targets automated
(7 wired and 6 run for real today; the finishing steps once CAD supplies the missing
collateral); zero failed steps reported as passed; each failure flagged within 5 minutes with
an AI-drafted, cited probable cause that engineers confirm on at least 80% of 20 or more
labelled cases; reviewers alerted within 1 minute, with every gate decision recorded against
a named engineer; and the Perforce source never modified.**

*Sign-off* here means PrimeTime DMSA timing, Formality equivalence and FC in-design ICV DRC.
Physical-verification sign-off (LVS, sign-off DRC), IR/EM and low-power sign-off stay manual.

### 3.2 Specific, measurable objectives

| # | Objective | Measurable target | Today | Addresses |
|---|---|---|---|---|
| O1a | **One-trigger flow in the project's control:** RTL → route_opt plus sanity, StarRC, PT DMSA, Formality r2g, RM summary | **13 of 23** targets; all 4 modules complete a single-trigger run; **0** hand-edited files | 7 wired, 6 run for real (debug, over 4 triggers) | P1, P5 |
| O1b | **Finishing steps, conditional:** chip_finish, icv_in_design, write_data, fm_g2g, timing_eco/PT-ECO, functional_eco | **19 of 23** once the §8.1 collateral is delivered | Blocked on collateral | P5 |
| O2 | **PASS/FAIL from evidence** | **0 false PASS** on a regression set of every past failure; false FAIL **≤ 5%** on known-good runs; sign-off thresholds agreed with the block owner (e.g. PT WNS ≥ 0 in all 5 corners, FM Succeeded, DRC 0), versioned in `fixed.yaml` | F1–F8 EXIST to route_opt; 3 of 3 failures that exited 0 caught | P3 |
| O3 | **Gates that never stall** (gates enabled) | Reviewer notified within **1 min**; **0** runs failed by an unattended timeout; **100%** of gate points passed carry a named human decision | No notification; 1 of 1 clock_opt gates timed out; run 4 passed 2 gate points with none recorded | P6 |
| O4 | **AI log triage** | Cited probable-cause note within **5 min** of each FAIL/WARN; **≥ 80%** correct on **≥ 20** labelled cases (replayed + injected). Correct = right error class and `file:line`, judged by an engineer who has not seen the note | PLANNED; 3 real log-visible failures so far | P4 |
| O5 | **AI QoR summary and next-step suggestion** at each gate and at finalize | **100%** of numbers match the fact table; whitelisted knobs only, never auto-applied; acceptance **≥ 50%**, and no accepted suggestion worsens WNS or TNS | Summarizer prototype (pico only) | P6 |
| O6 | **Run and gate history** | **100%** of runs record inputs, config sha256, manifest, patch diff, scripts and every gate decision (never overwritten); survives a host move | Per-run records EXIST; gate records overwritten on resume | P7 |
| O7 | **Multi-user use** | Per-user login; runs attributed; **≥ 3** engineers other than the author each complete **≥ 1** real run within 4 weeks of the per-user release (R5) | Single user, localhost | P7 |
| O8 | **Earlier failure detection** | Fatal patterns flagged within **5 min** of appearing; SDC clock-port check before init_design | Detection at step end | P4 |
| O9 | **Controlled knobs and versions** | One pinned FC version per project profile; every knob change via the form or a whitelisted conf, recorded in the patch diff | fc_shell pinned (`fixed.yaml:25-26`); 3 knobs patched | P2 |
| S1 | *Stretch:* NL run requests, cross-run comparison | Time for a first-time user to launch a valid run; any two run tags compared | PLANNED | P1, P7 |

P8 is handled by the existing license queueing and the risks in §10.

### 3.3 Expected benefits
- **Engineer time:** hours saved per run × runs per month, once touch-time has a baseline (§9).
- **Compute and licenses:** fewer hours burned after a fatal error (corvi ran 1.36 h).
- **Quality and traceability:** no failure carried forward as a pass; every run and decision auditable and re-runnable.

---

## 4. Scope

### 4.1 In scope
- The 4 Raven modules and the 19 RM flow targets of O1a/O1b.
- Orchestration: precheck, workspace isolation, knob patching, license queueing, launch and monitoring on mipspd81–85, PASS/FAIL grading, resume, retry, kill.
- Human review gates with notifications and full decision history.
- An advisory AI layer: log triage, QoR interpretation, summaries, suggestions.
- Run records, a history DB, dashboards and readouts; per-user deployment, then a shared service (§5.4).

### 4.2 Out of scope
- Editing RTL, SDC, DEF or RM scripts, by the tool or the AI; floorplan creation (the DEF is an input).
- LVS (no RM script: `grep check_lvs|icv_lvs|run_lvs` → 0 files) and sign-off DRC; VC-LP, RedHawk, RHSC and TetraMAX (`UPF_MODE none`, `DS:90`; empty `REDHAWK_TECH_FILE`, `DS:457`).
- Supplying foundry/CAD collateral. The tool detects its absence; it does not invent it.
- Other technologies until a project-profile layer exists (R5), and autonomous QoR loops without a human decision.

### 4.3 Flow coverage table

Legend: ✅ automated and run for real (unless noted) · 🟡 partial · ⬜ planned · ➖ out of scope.

| Phase | Step | Status | Notes / evidence |
|---|---|---|---|
| Pre-flow | Host choice | 🟡 | User picks; precheck probes load, RAM, nproc on 5 hosts (`PF/pdflow/precheck.py`); automatic choice PLANNED as a rule |
| | Environment, workspace copy, source manifest | ✅ | Pinned fc_shell, `module purge` (`PF/pdflow/scripts.py:41-44,166-170`); 973-file manifest (`workspace.py:102-140`) |
| | Patch clock, cores, S19 toggle | ✅ | Regexes that must match once; diff kept (`patch.py`) |
| | Other knobs (271 + 29 + 25 hooks) | ⬜ | Not exposed |
| | Reference libraries / NDM | 🟡 | Precheck only (warned about `macros_new.ndm`, §7.2 row 1) |
| | License queueing | ✅ | 12 h; soft timeout excludes queue (`fixed.yaml:27-30`; `runner.py:240-264`) |
| | RTL build | ✅ | 7 PASS rules (`checks.py:66-120`) |
| | sanity / sanity_FC | ⬜ | `MK:48-55` |
| FC implementation | Floorplan / DP input | 🟡 | Input only: `INIT_DESIGN_INPUT RTL` (`DS:21`), DEF (`DS:161`), bounds (`DS:175`); precheck checks the DEF exists |
| | init_design (incl. MCMM, TLU+, PG) | ✅ | Real runs on debug, trf, corvi; MCMM/TLU+/PG scripts run inside it (`DS:100,107,188`), no content checks |
| | compile (incl. DFT insertion, DFT DRC) + gate | ✅ | Real runs on debug, trf (`DS:216-218,601`); `continue_past_s19` |
| | place_opt | ✅ marker only | Not a DAG task; compile writes its marker (`UNIFIED_FLOW true`, `fc_setup.tcl:24`; `scripts.py:147-148`) |
| | clock_opt_cts, clock_opt_opto + gate | ✅ | Real runs (debug) |
| | route_auto | ✅ | First real run (debug run 4): WARN, rc 0, 1,962 s, 0 open nets, 304 DRCs |
| | route_opt | ✅ | First real run (debug run 4): WARN, rc 0, 1,301 s, 0 open nets, 399 DRCs; open nets FAIL, DRCs WARN (`checks.py:256-273`) |
| | chip_finish · icv_in_design · write_data | ⬜ | Filler script missing (`S/chip_finish.tcl:70`); runsets empty (`DS:387,415`); GDS map empty (`DS:428`) |
| | timing_eco (incl. eco_opt) · functional_eco | ⬜ | eco_opt runs in timing_eco without a PT change file and needs `ECO_OPT_PT_PATH` (`MK:121-125`; `DS:511,537`); input block icv_in_design (`DS:619`) |
| Sign-off | StarRC · PT DMSA (5 corners) · PT-ECO | ⬜ | `run_starrc.tcl:75-100` writes into scripts_block (redirect to run copy); `pt_setup.tcl:63,92` |
| | ECO closure loop (PT-ECO → timing_eco → StarRC → PT) | ⬜ | With an iteration cap |
| | Formality r2g / g2g | ⬜ | `.svf` files already produced |
| | Power | 🟡 | `report_power` written, not parsed or gated |
| | LVS, sign-off DRC, VC-LP, RedHawk, RHSC, TetraMAX | ➖ | §4.2 |
| Post-flow | QoR extraction + gate page; run summary + `runs_index.tsv` | ✅ | `qor.py`, `summary.py` |
| | Gate notifications | ⬜ | `PF/PLAN.md:278` |
| | AI summary · AI triage · AI QoR note | 🟡 · ⬜ · ⬜ | Summarizer works on pico only |
| | NL request · cross-run comparison | ⬜ | Stretch. FC `compare_qor_data` is commented out (`S/report_qor.tcl:34`); `write_qor_data` runs (`:13-28`; `DS:574`) |
| | Dashboards, readout deck, backup/clean | 🟡 / ⬜ | Standalone tools (Appendix B); backup/clean targets (`MK:165-175`) not wired; no disk clean-up (`PF/PLAN.md:268`) |

**Coverage today:** 7 of 23 Makefile flow targets wired (30%); 6 run for real (all 6 DAG FC
steps, the last four on debug only); place_opt is marker-only. **Target:** 13 of 23 (O1a), then
19 of 23 (O1b). Excluded: tmax, redhawk_in_design_pnr, rhsc_in_design_pnr, vc_lp.

### 4.4 Constraints and assumptions
- Perforce is read only: the tool works on a patched copy and changes **0** source files.
- Step scripts run in csh on mipspd81–85, launched by ssh (no batch scheduler). System Python is 3.6.8; the orchestrator needs the 3.11 venv.
- LLM calls use only the approved internal gateway; the AI is advisory with no write path. RM scripts are Synopsys-copyrighted and stay out of every repo.

---

## 5. Proposed Solution

Three layers: **deterministic code decides; the AI advises; the engineer approves.**

```
  Engineer ── Airflow trigger form / CLI (bin/pdflow.sh) / NL request* ──┐
                                                                          │ validated conf
 ┌────────────────────── (a) ORCHESTRATION LAYER  [EXISTS to route_opt] ──▼──────────────────┐
 │ resolve_inputs → precheck → prepare_workspace → build_rtl → init_design → compile          │
 │   → [GATE] → clock_opt_cts → clock_opt_opto → [GATE] → route_auto → route_opt              │
 │   → chip_finish* → icv_in_design* → write_data* → StarRC* → PT* → Formality* → finalize    │
 │ PdStepOperator: ssh launch on mipspd81-85, re-attach/adopt, F1-F8 PASS/FAIL, kill safety   │
 └───────┬──────────────────────────────▲───────────────────────────────┬────────────────────┘
         │ log/report excerpts, facts   │ advisory notes (cited,        │ run.json, qor/*.json
         ▼                              │ number-checked)               ▼
 ┌─ (b) AI LAYER  [PLANNED]  ───────────┴──┐        ┌─ (c) DATA & REPORTING  [partial] ──────┐
 │ Log-triage agent*     (root cause)      │        │ run.json · qor/<step>.json · summary.md│
 │ QoR interpreter*      (gate note)       │◄──────►│ runs_index.tsv · run-history DB*        │
 │ Report summarizer (EXISTS: pico, not in │  facts │ dashboards* · readout deck*             │
 │   Raven DAG)  Recommender*  NL parser*  │        └────────────────────────────────────────┘
 └─────────────────────────────────────────┘
   Source: Perforce rm_fc_cad (READ ONLY) ──copy──► RW/<run_tag>/rm_fc_cad (patched run copy)
   (* = planned)
```

### 5.1 (a) Deterministic orchestration layer — EXISTS up to route_opt
- **Pipeline:** an Apache Airflow 3 DAG (directed acyclic graph of tasks), `raven_pd_flow`, with 13 tasks (`DAG:244-245`) and a 12-parameter trigger form (`DAG:54-109`); all other values live in `config/fixed.yaml`.
- **Step operator** (`PF/pdflow/airflow_ops.py`): launches FC detached over ssh in a clean `env -i`; re-attaches to or adopts jobs after an Airflow restart and never launches a step twice (`runner.py:182-200`); kills the whole job session (`remote.py:65-87`); soft timeout excludes license-queue time.
- **PASS/FAIL:** 7 build rules and F1–F8 per FC step. A step passes only if exit code, fresh marker, FC's "Thank you" line, fatal-pattern scan, echoed clock and cores, expected outputs and report_qor clock period all agree (`PF/PLAN.md:166-173`).
- **Gates:** Airflow HITL `ApprovalOperator` with a QoR Markdown page; decisions go to `run.json.gates` (`airflow_ops.py:208-211,213-259`). Gates are on by default but can be switched off in the trigger form (`DAG:101-105`). A resume past a gate auto-approves it (`context.py:179-181`; `airflow_ops.py:216-220`) and the new record **replaces** the earlier decision (§7.2 row 8).
- **PLANNED:** finishing and sign-off operators with their own PASS rules; notifications; append-only gate history, a resume past a gate recorded as `approved-by-resume` by the named triggering user, and explicit approval required after a timed-out gate; in-flight log scan; per-module error-ID baselines (`fixed.yaml:143-145`).

### 5.2 (b) AI layer — PLANNED (one standalone prototype exists)
**Rules.** (1) The AI receives only run artefacts: log and report excerpts, qor JSON and run.json
fields. It never receives RTL or RM script text; code finds referenced files and passes
`file:line` references, and an engineer opens the source. (2) It never changes a verdict and has
no write path. (3) Every claim cites `file:line`; every number must match the fact table ("code
computes, LLM explains", `MW/WORKFLOW.md:152-190`). (4) If the LLM fails, the table is shown
alone. Every request is logged (content sent, tokens, latency).

| AI feature | Reads | Produces | Human check | Measured by |
|---|---|---|---|---|
| Log triage | First-error excerpts, FC message table, `file:line` refs from code | Probable cause after each FAIL/WARN, e.g. corvi VER-294 → `sam_exu_gru_exc_decdr.v:85` | Engineer confirms or corrects on the gate page | % correct on ≥ 20 labelled cases, per class |
| QoR interpreter | `qor/<step>.json`, report_qor excerpts | Gate note, e.g. "util 91% after clock_opt_opto → check floorplan before route" | Number check + gate approver | 100% numbers matched; reviewer time per gate |
| Report summarizer | Step reports, within a token budget | Summary in `summary.md` (port of `MW/summarize_reports.py`) | Number check | % numbers matched |
| Recommender | QoR facts, whitelisted knobs (`fc_setup.tcl:24-108`, Clk_period, hooks) | Next-run conf JSON, validated by `resolve_inputs` and precheck | Engineer accepts or rejects; never auto-applied | Acceptance rate; WNS/TNS change |
| NL run parser (stretch) | Plain-English request, host loads | Trigger JSON | User confirms before launch | % parsed with no edit |

**Not AI (deterministic):** the noise classifier is an error-ID baseline lookup (`fixed.yaml:143-145`);
the cross-run comparer is a JSON diff behind a new subcommand `pdflow.sh compare` (planned); host
choice is a rule over load, RAM and nproc. An LLM may add a narrative on top.

**LLM access:** the internal Anthropic-compatible gateway used by the summarizer
(`MW/summarize_reports.py:39-41`), with a service credential from an environment variable, once
IT/legal clear design data for it.

### 5.3 (c) Data and reporting layer — partial
- **EXISTS:** per run, `run.json` (inputs, fixed-config snapshot and sha256, history, steps, gates, patches), `qor/<step>.json`, `summary.md`, `patches/<module>.diff`, re-runnable `scripts/NN_<step>.csh`; and `RW/runs_index.tsv`.
- **PLANNED:** a run-history DB (Postgres) with a `user` column and append-only gate decisions; dashboard links; an auto-drafted readout deck.

### 5.4 Deployment and usage model
- **Users and prerequisites:** Raven PD engineers with a synced Raven Perforce workspace, ssh `BatchMode` keys to mipspd81–85, csh, FC licenses and scratch (10/50/150 GB for S/M/L, `fixed.yaml:121`). Paths are currently the author's: `mips_home` and `runs_root` (`fixed.yaml:10,16`).
- **Step 1, per-user install (R5):** clone, `airflow/setup.sh`, and a per-user overlay for `mips_home`, `runs_root` and the UI port. Blocking changes: configurable paths and ports, path-independent tests, `.gitignore` and `requirements.txt`, RM-free fixtures.
- **Shared resources:** pools are per Airflow instance (`pd_fc`=2, `pd_build`=2; `airflow/setup.sh:43-44`), so N installs allow 2N concurrent FC jobs. Add a global cap (shared pool or per-host lock file).
- **Step 2, shared service:** jobs run as the requesting user; gate approvers assigned per run; LDAP login, TLS, Postgres, a service account; a named maintainer, issue tracker and versioned releases; IT/CAD approval.
- **Upload checklist:** (1) `chmod 600` the generated Airflow login file in `PF/airflow/home/`, still `-rw-rw-r--` on 2026-10-05; (2) never commit `airflow/home/`, the venv, run directories or the 12 RM-copyrighted fixtures; (3) `MIPS/indus` deploys `main` to an EC2 host on every push with no path filter (`IN/.github/workflows/deploy.yml:3-6,14`), so ask its owners to restrict it before any PR, or publish elsewhere; (4) publish a sanitized copy of this document (relative paths, no hostnames, no credential locations).

---

## 6. Tools & Technologies

Core stack (adjacent tools: Appendix B).

| Category | Tool | Used for | Status |
|---|---|---|---|
| EDA — implementation | **Synopsys Fusion Compiler U-2022.12-SP6**, pinned fc_shell path | Raven init_design → route_opt (`fixed.yaml:25-26`; `RW/debug…001616/…/logs/compile.log:6`) | In use |
| | Synopsys RM scripts R-2020.09-SP6 | Step Tcl per module (`MK:4`) | In use (read only) |
| EDA — RTL build | pyBuildSim `build.py`, VCS X-2025.06-1 | RTL filelists (`RW/debug…001616/logs/build.source_env.log:31,50`) | In use |
| EDA — sign-off | StarRC `S-2021.06-SP5-1`, PrimeTime DMSA + PT-ECO `2021.06-SP5-1` (`CV/run.csh:10-11`); Formality; IC Validator (in-design) | Extraction, STA, ECO, equivalence, in-design DRC (`MK:98-104,145-149`) | Planned; versions to pin in `fixed.yaml`; ICV runsets missing |
| Orchestration | **Apache Airflow 3.3.2** (task-sdk 1.3.2, providers-standard 1.19.0 HITL), LocalExecutor, SQLite | DAG, gates, UI on port 8081 via ssh tunnel | In use; Postgres planned |
| | tmux, ssh `BatchMode`, `setsid` / `env -i`; GNU Make | Scheduler session, detached jobs; Make is the interface being replaced | In use / reference |
| Languages and libraries | Python 3.11.13 venv: pytest 9.1.1, PyYAML 6.0.3, requests 2.34.2 | `pdflow` (14 modules + DAG = 2,756 lines), tests, config | In use |
| | Python 3.6.8 (system), Tcl, csh/sh, YAML | Summarizer; RM steps; launchers; `fixed.yaml` (147 lines) | In use |
| AI | Internal Anthropic-compatible gateway, model `claude-opus-4-8`, plain `requests` | `summarize_reports.py` (`:39-41,221-238`) | Prototype |
| | Anthropic Python SDK (not installed; 1.x needs Python ≥ 3.10, so the venv qualifies): structured outputs (`output_config.format` / `messages.parse`) for fact-table JSON, tool use for triage, prompt caching for the fixed system prompt, `count_tokens` instead of the 500 KB byte cap (`summarize_reports.py:35`), Message Batches for finalize summaries | Triage, QoR notes, summaries | Planned; confirm gateway support per feature; model ID in `fixed.yaml`, eval set re-run on every change |
| Notification | Email (SMTP) or chat webhook | Gate alerts ≤ 1 min | Planned (channel with IT) |
| Infrastructure | mipscs83 (Airflow); mipspd81–85, Rocky 8.10 (128/128/32/32/32 cores); Environment Modules; Perforce `jsingh9_raven_ws2` (read only); GitHub `MIPS/indus`; license queue (43,200 s) | Scheduler, compute, versions, source, upload target | In use |
| | PostgreSQL, LDAP, TLS proxy, systemd | Shared service | Planned |

---

## 7. Current Progress

### 7.1 Orchestration core — `pd_flow_airflow` (EXISTS)
- **Code:** 14 modules plus the DAG, **2,756 lines**; the DAG and `context.py` were last edited 2026-10-04 at 23:43 and 23:46.
- **Working:** one-trigger run from the RTL build to route_opt; validated form; precheck (paths, SDC, DEF, patch dry run, disk, 5-host probe, FC environment and license, live-job conflicts); fresh patched copy per run; F1–F8; 2 QoR gates (72 h timeout); resume with earlier steps verified; re-attach/adopt; safe kill; license queueing; pools; an 8-subcommand CLI (`list`, `status`, `qor`, `kill`, `gates`, `review`, `approve`, `reject`).
- **Tests:** **49 passed** in about 1 s (34 functions, parametrised): patches on the 4 real setup files, `csh -n`/`sh -n` on generated scripts, QoR parsers on real reports, runner re-attach/adopt/lost/license/unreachable-host cases. One assertion always passes (`or True`, `tests/test_pdflow.py:218`) and needs fixing.
- **Hardening:** an adversarial review confirmed 37 issues, all fixed (`PF/PLAN.md:306`).
- **Source untouched:** all 973 manifest files in `P4` match their sha256 (0 mismatches).
- **No AI inside yet:** no LLM client code in the package; gate text is deterministic (`qor.py`).

### 7.2 Real-run evidence — `RW/runs_index.tsv` (8 rows: 6 real DAG runs, 2 dry runs)

| # | Run | Setup | Steps reached | Result | What the tool caught |
|---|---|---|---|---|---|
| 1 | dry-debug…001406 | debug, 1.5 ns, pd81 | all 7 dry | DRY-PASS | Only `design_setup.tcl`/`fc_setup.tcl` patched; precheck warned of a missing `macros_new.ndm` |
| 2 | debug…001616 run 1 | debug, 2 ns, pd81 | build 67 s (37 RTL files) | PASS | — |
| 3 | debug run 2 | | init_design WARN 782 s; compile **FAIL** | FAIL | **S19 stop:** no exit-code failure recorded (`run.json` `history[1].problems`), marker missing, "Rule S19 violation exists and hence exiting" (`…/logs/compile.log.20260930-011128:7067`). Led to `continue_past_s19` |
| 4 | trf…004850 | trf, 1.25 ns, pd81 | build 32 s; init_design WARN 182 s; compile **FAIL** 587 s | FAIL | **S19 stop with rc 0** (`status/compile.rc`; `…/logs/compile.log:7969`) |
| 5 | dry-cic…033518 | cic, 1.25 ns, pd83 | all dry | DRY-PASS | Patched setup files and the `compile.tcl` toggle |
| 6 | corvi…012905 | corvi, `rvn_inova`, 2 ns, pd84 | build 137 s (392 RTL files); init_design **FAIL** after 1.36 h | FAIL | **RTL errors behind rc 0, marker and "Thank you":** 8 × VER-294, 7 in `raven/src/main/rvn_isu/sam_exu_gru_exc_decdr.v` (first at :85; `init_design.log:1818-1824`) and 1 in `raven/src/main/rvn_lsu/rvn_lsu.sv:24` (`:1828`), plus 12 × VER-260 (`:1829-1840`) |
| 7 | debug run 3 (from compile, S19 continue ON) | | compile WARN 594 s; clock_opt_cts WARN 296 s; clock_opt_opto OK 869 s | FAIL | **Gate timeout:** compile gate approved after 2 h 33 min; clock_opt gate unanswered for 72 h (`runs_index.tsv:8`) |
| 8 | debug run 4 (`start_from=route_auto`, 2026-10-04 23:44) | pd81 | route_auto WARN 1,962 s (incl. 6.3 min queue; 304 DRCs); route_opt WARN 1,301 s (399 DRCs; CMD-007, CMD-013 once each); 0 open nets | PASS | **First real routing.** Both gates auto-skipped because their steps were reused (`context.py:179-181`); `run.json.gates` now calls the timed-out clock_opt gate "already reviewed", and the run is PASS (`runs_index.tsv:9`) with no human decision at that gate. The wording was changed in code at 23:46; the skip and overwrite remain |

- **Outcome:** **3 of 3** real step failures graded FAIL although each exited 0 (rows 3, 4, 6). An exit-code check would have passed all 3; under `make`, corvi would have gone on into compile and both S19 stops would have silently re-run compile.
- **Checks confirmed:** F5 confirmed the requested Clk_period and max_cores in every real FC step recorded in run.json; F7 confirmed the report_qor Clk period (2.0 ns) in the 5 debug steps that write report_qor (compile through route_opt).
- **Debug QoR** (`run.json` `steps.<step>.facts.qor`):

| Step | Setup WNS / TNS (ns) | Hold WNS (ns) | Utilization |
|---|---|---|---|
| compile | −0.89 / −4.297 | −1.476 | 0.766 |
| clock_opt_cts | −0.384 / −0.749 | −0.16 | 0.779 |
| clock_opt_opto | −0.542 / −1.111 | −0.134 | 0.912 |
| route_auto | −0.527 / −1.139 | −0.143 | 0.912 |
| route_opt | −0.389 / −0.759 | −0.141 | 0.916 |

- **Not proven yet:** a single uninterrupted trigger from RTL to route_opt. The debug result took 4 triggers and 5.0 days elapsed for 1.63 h of compute; no other module has passed compile.

### 7.3 AI report summarizer — `MW/summarize_reports.py` (EXISTS, prototype)
- 454 lines: list → extract → LLM call → save (the "Stage 1" docstring is stale); defaults to the pico reports folder (`:20`).
- Anthropic Messages API through the internal gateway, model `claude-opus-4-8`, `max_tokens` 4096, 180 s timeout (`:39-41,63-67`); sends each report's full text (`:221-238`); grounded prompt (`:182-195`).
- 500 KB cap per file (`:35`): `pin_tran_cap.rpt` (now 3.76 MB) is above it and appears in neither summary's report list.
- 2 real summaries (98 and 134 lines) over 7 pico reports. No chunking, structured output, number check or tests.

### 7.4 Deployment readiness
Audited, **not uploaded**. The folder is 324 MB, 290 MB of it the venv (`du -sh`). Blocking changes
and the upload checklist are in §5.4.

---

## 8. Roadmap

Milestones are in order; dates and effort are to be agreed with the sponsor. (R-numbers are this
document's; `PLAN.md` has its own M1–M7, where M6 "full runs" is still marked pending, `PF/PLAN.md:300`.)

| Milestone | Content | Exit criterion | Status |
|---|---|---|---|
| **R1** Core to route_opt | DAG, operators, F1–F8, gates, resume/kill, CLI, tests | Single-trigger real run to route_opt, both gates answered by a named reviewer; append-only gate history; notifications; disk clean-up; always-true test fixed | Code done; debug RTL → route_opt over 4 triggers |
| **R2a** Sign-off in our control (O1a) | sanity, StarRC, PT DMSA, Formality r2g, RM summary, PASS thresholds | 13 of 23 targets; one module RTL → PT/FM from one trigger | Planned |
| **R2b** Finishing (O1b) | chip_finish, icv_in_design, write_data, fm_g2g, timing_eco/PT-ECO, functional_eco; precheck names missing collateral | 19 of 23, once §8.1 items arrive | Blocked |
| **R3** AI triage and summaries | Summarizer port; fact table + number check; triage agent; notes on gate page and `summary.md` | ≥ 20 labelled cases, ≥ 80% correct; 100% number check; 0 changed verdicts; gateway clearance | Prototype exists |
| **R4** AI suggestions | QoR interpreter, recommender; stretch NL and compare; ECO-loop assistant after R2b | Acceptance ≥ 50%; no accepted suggestion worsens WNS/TNS | Planned |
| **R5** Multi-user | Per-user install with a global FC cap; then the shared service | ≥ 3 other engineers each complete a real run within 4 weeks; IT/CAD sign-off | Audit done |
| **R6** Dashboards and history | History DB with user column; run/QoR dashboard; readout deck | Every run visible with inputs, verdict, QoR, gate decisions, AI notes | Planned |

### 8.1 Dependencies and asks

| Item | Needed for | Owner | By |
|---|---|---|---|
| Filler script `chip_finish.tcl.sg_7nm_t.filler_insertion` (`S/chip_finish.tcl:70`) | chip_finish | CAD/RM (to be named) | R2b |
| ICV DRC and metal-fill runsets (`DS:387,415`); GDS layer map (`DS:428`); `ECO_OPT_PT_PATH` (`DS:511`) | icv_in_design, write_data, eco_opt | CAD / foundry kit owner | R2b |
| Design-data clearance for the LLM gateway | AI layer | IT / legal | R3 |
| Service host, service account, LDAP, Postgres; deploy-workflow path filter | Shared service; upload | IT; indus owners | R5 |
| Sponsor, reviewer, second maintainer | Priorities, review, bus factor | PD management | R2a |

---

## 9. Success Metrics / KPIs

| KPI | Baseline (measured) | Target | How measured |
|---|---|---|---|
| RM flow targets automated | 7 / 23 wired; 6 run for real | 13 / 23, then 19 / 23 | §4.3 |
| Manual actions; hand-edited files | ≈ 30 / ≈ 44 actions; 3–4 / ≥ 6 files | 1 trigger + gate decisions; 0 files | Operator count; patch diff |
| Engineer touch-time per run | Not measured: time one manual debug run to clock_opt_opto before R2a | ≥ 70% reduction | Time log |
| Turnaround RTL → route_opt | debug: 1.63 h compute, 5.0 days elapsed, 4 triggers (n = 1) | 1 trigger; elapsed ≈ compute + queue + gate decision time | Status epochs |
| Compute lost on failed runs | Not tracked (e.g. corvi 1.36 h) | Reported per run; reduced by in-flight detection | FAIL-step durations |
| False PASS / false FAIL | Exit code passes 3 of 3 failures; tool 0 false PASS; false FAIL not measured | 0 / ≤ 5% | Replay and fault injection |
| QoR parity vs manual Makefile run | Not measured | WNS, TNS, util within run-to-run noise | Side-by-side run |
| AI cause correct | 0% (no triage) | ≥ 80% of ≥ 20 cases | Blind engineer review |
| Time to detect a failure | Step end (corvi 1.36 h) | ≤ 5 min after the fatal message | Log time vs alert time |
| Gate response | 2 h 33 min; 72 h timeout (n = 1) | Alert ≤ 1 min; 0 timeout failures | Gate open vs decision |
| Gate points with a named human decision | 0 of 2 in run 4 | 100% | Gate history |
| AI numeric accuracy; cost and latency | Not measured | 100%; tokens, cost, seconds per run reported, budget set at R3 | Number check; request log |
| Infrastructure-caused failures | Not tracked (mipscs82 outage) | Reported per 10 runs | Failure class |
| Source-workspace modifications | 0 of 973 files | 0 | `sha256sum -c` |
| Adoption | 1 user; 6 real DAG runs | ≥ 3 other engineers within 4 weeks of R5 | `user` column |

---

## 10. Risks & Mitigations

| Risk | Evidence | Mitigation |
|---|---|---|
| **Gates bypassed** | Gates can be switched off (`DAG:101-105`); resume auto-approves and overwrites (run 4) | Append-only history; resume recorded as a named approval; explicit approval after timeout |
| **License contention, oversubscription** | Queues of 9.6, 1.0, 6.3 min; pools per install | Queue (12 h), soft timeout excludes queue; global FC job cap |
| **Late failure** | corvi 1.36 h before FAIL | Precheck; in-flight scan with early kill; SDC clock-port check; resume |
| **LLM hallucination, model or gateway change** | No number check today; model pinned in code (`summarize_reports.py:41`) | Code computes, LLM explains; number check; citations; verdict stays deterministic; model ID in `fixed.yaml`; eval re-run on change; table-only fallback |
| **Design-data confidentiality** | Full reports sent (`summarize_reports.py:221-238`); token can fall back to a personal settings file (`:44-50`) | Written IT/legal clearance; service credential in an env var; excerpts only, never RTL or RM text; log every request |
| **AI evaluation too small** | 3 real log-visible failures | ≥ 20 cases before R3 exit, incl. injected bad SDC clock port, wrong TECH_DIR/DESIGN_NAME, missing NDM, license timeout, open nets, missing marker |
| **Fragile patches** | Regexes must match exactly once (`patch.py`) | Patch dry run in precheck names the broken regex |
| **RM licensing** | 12 RM-copyrighted fixtures; run directories hold RM copies (`RW` 4.9 GB) | Keep RM files and run directories out of every repo; tests use the user's workspace or skip |
| **Security and publication** | Single `admin` login; login file world-readable; indus CI deploys `main` to EC2; at least one team script holds a hard-coded DB password literal | §5.4 checklist; per-user first; shared service only with LDAP/TLS |
| **Infrastructure** | mipscs82 outage lost SQLite history; scratch 89% full | Service host under systemd; Postgres; disk report and clean-up |
| **Missing collateral** | Filler script, runsets, GDS map, ECO path | O1a/O1b split; precheck names each; §8.1 owners |
| **Bus factor, adoption** | One developer wrote all 2,756 lines | Second maintainer and runbook; QoR-parity runs to build trust |
| **Tool-version sprawl** | 3 FC versions | Pin per profile; unpinned fc_shell already refused (`fixed.yaml:26`) |

---

## 11. Deliverables

1. **Orchestrator** `pdflow` + DAG `raven_pd_flow` (EXISTS to route_opt; sign-off in R2a/R2b).
2. **AI advisory module** behind the number-check guard (PLANNED).
3. **Run-history store and dashboard** with users and append-only gate decisions (PLANNED).
4. **Deployable package** (§5.4) as a PR to `github.com/MIPS/indus`, then a shared-service design for IT/CAD (PLANNED).
5. **Tests:** unit tests (49 today), fault-injection and replay tests, the AI evaluation set.
6. **Documentation** (this statement, `README.md`, `PLAN.md`) and a **KPI report** before and after rollout.

---

## Appendix A — Basis of measurement

Read-only commands, using the path abbreviations above. Figures cited inline to a `file:line` are not repeated.

| Figure | Value | Command / source |
|---|---|---|
| Makefile targets | 30 / 23 flow | `grep -cE '^[A-Za-z0-9_]+:' MK`; 7 housekeeping listed by hand |
| Targets wired | 7 of 23 | `DAG:244-245` mapped to `MK` targets |
| design_setup knobs | 271; 113 empty; 25 hooks, 11 filled | `grep -cE '^\s*set [A-Z_]+' DS`; `grep -cE '^\s*set [A-Z][A-Z0-9_]*\s+""\s*(;?\s*#.*)?$' DS`; `grep -cE '^\s*set [A-Z_]*(PRE\|POST)_SCRIPT\|USER_HOOK\|_HOOK' DS`, then `\| grep -vcE '\s""'` |
| fc_setup knobs; build configs | 29; 41 | `grep -cE '^\s*set ' CV/scripts_block/rm_setup/fc_setup.tcl`; `ls …/raven/src/config \| wc -l` |
| `\| tee` / pipefail | 23 / 0 | `grep -c '\| *tee' MK`; `grep -c pipefail MK` |
| RM-error lines that abort | 28 of 148, 32 scripts | `grep -h 'RM-error' S/*.tcl \| wc -l`; awk per file over the 3 lines after each `RM-error` for `exit`/`return` |
| Script differences | §1 | `diff -rq CV/scripts_block P4/<module>/scripts_block` |
| Manual actions | ≈ 18 / 30 / 44 | Counted from `CV/run.csh:1-28`, `PF/PLAN.md:7-13`, `P4/block/README:9-29` |
| corvi log | 71,431 lines; 152 `^Error`; 8 VER-294; 12 VER-260; first at 1,818 | `wc -l`; `grep -c '^Error'`; `grep '^Error' \| grep -c VER-294` (VER-260); `grep -n -m1 '^Error'` |
| S19 violation counts | 5 (debug), 1 (trf) | `grep 'Rule S19 .* was violated' rpts/compile/dft_drc_full.rpt` |
| Step runtimes, queues | corvi init_design 4,883 s (1.36 h); debug FC 782 + 594 + 296 + 869 + 1,962 + 1,301 = 5,804 s, + build 67 = 5,871 s (1.63 h); queues 9.6 / 1.0 / 6.3 min | `run.json` `steps.*.duration_s`, `facts.license_queue_min` |
| Debug elapsed | 433,407 s (5.0 days) | `status/route_opt.end − status/build.start` |
| Gate wait (run 3) | 9,197 of 10,984 s (84%) | `status/clock_opt_cts.start − compile.end`; `clock_opt_opto.end − compile.start` |
| Gate timeout | 72 h | `fixed.yaml:125`; `runs_index.tsv:8` |
| Run index | 8 rows (6 real, 2 dry) | `cat RW/runs_index.tsv` |
| Perforce unchanged | 973 files, 0 mismatches | `cd P4 && sha256sum -c --quiet RW/dry-debug…/src_manifest.sha256` |
| Code size | 2,756 lines | `wc -l pdflow/*.py airflow/dags/raven_pd_flow_dag.py` |
| Tests | 49 passed (about 1 s); 34 functions | `env PYTHONDONTWRITEBYTECODE=1 airflow/venv/bin/python -B -m pytest -q -p no:cacheprovider tests`; `grep -c 'def test_' tests/test_pdflow.py` |
| No LLM code | 0 matches | `grep -rniE 'anthropic\|openai\|llm\|claude\|requests\.post' pdflow/ airflow/dags/ bin/ config/` |
| Venv libraries | pytest 9.1.1, PyYAML 6.0.3, requests 2.34.2; no `anthropic` | `importlib.metadata.version(...)` in the venv |
| Summarizer | 454 lines; 2 outputs (98, 134 lines) | `wc -l`; `ls ~/Desktop/pd_report_summary_*` |
| Debug report files | 240 | `find rpts/<step> -type f \| wc -l` over the 6 step directories |
| Sizes | `RW` 4.9 GB; `PF` 324 MB (venv 290 MB); scratch 89% | `du -sh`; `df -h /home/jsingh9.scratch` |
| Login-file mode | `-rw-rw-r--` (contents not read) | `ls -l PF/airflow/home/` |
| RM-copyrighted fixtures | 12 | `grep -rlE 'Copyright \(C\) [0-9-]+ Synopsys' tests/fixtures \| wc -l` |
| Appendix B sizes | 356 / 438 / 127 / 668 / 230 / 760 / 2,448 / 1,439 / 353 lines | `wc -l` on each named file (`sta_path_insight.py` is under `IN/adhoc/rm_power/scripts/sta_path_insight/`) |

---

## Appendix B — Adjacent tools and prior work

**jsingh9's own work**
- **Pico AI-summary hook:** `IN/pico_fc_flow/rm_fc/scripts/fc_run.tcl` runs compile_fusion → clock_opt → route_auto (`:42-87`); the added hook (`:89-136`, uncommitted) asks `[y/N]` in a TTY, skips in batch, and `PD_AUTO_SUMMARY=1` forces it.
- **Power tools:** `MW/power_report_parser.py` (356 lines), `power_report_db.py` (438), `test_power_report_db.py` (127): PrimeTime-PX reports into a 22-column SQLite table (2 picorv32 rows). Design docs `MW/WORKFLOW.md` (230 lines), `SPEC.md`, `BuildPowerReportDeck.bas` (VBA, 668 lines); diff, similarity and explain steps not built (`WORKFLOW.md:196-202`).
- **freq_hist_jenkins** (`MW/freq_hist_jenkins/`): one comparison engine behind a Jenkins pipeline (never run in real Jenkins), a local web app (`local/serve.py`, 760 lines) and the Airflow DAG `freq_hist_compare` (4 runs, incl. a real corvi compile comparison).
- **`make_ppt.py`** (python-pptx, 353 lines, metrics hard-coded): local and untracked in `IN/pico_fc_flow/rm_fc/tmp/`.

**Team assets (not jsingh9's code):** `IN/sta_timing_dashboard` (Streamlit + Plotly, 2,448 lines),
`sta_path_insight.py` (1,439 lines), `adhoc/run_track` (QoR CSV → MySQL), `adhoc/rm_power`.
