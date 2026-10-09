"""
raven_pd_flow — Raven module: RTL build (pyBuildSim) -> Fusion Compiler RM up to route_opt.

Trigger with "Trigger DAG w/ config". You choose the pd host, the module and the per-run
knobs; everything else is predefined in config/fixed.yaml. The flow:

  resolve_inputs -> precheck -> prepare_workspace -> build_rtl -> fc_init_design -> fc_compile
  -> gate_compile -> fc_clock_opt_cts -> fc_clock_opt_opto -> gate_clock_opt
  -> fc_route_auto -> fc_route_opt -> finalize

Each run lives in /home/jsingh9.scratch/raven_local_ws/<run_tag>/ (a fresh copy of
$MIPS_HOME/rm_fc_cad -- the Perforce workspace is never modified). Tool steps run DETACHED
on the chosen pd host over ssh, so they survive an Airflow restart ("Clear" re-attaches).
The logic lives in pdflow/ (plain Python); this file only wires it into Airflow.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # project root -> `import pdflow`

import pendulum
from airflow.sdk import Param, dag, task
from airflow.sdk.exceptions import AirflowFailException

from pdflow.airflow_ops import PdQorGate, PdStepOperator, dag_run_finished
from pdflow.config import FC_STEPS, STEPS, list_build_configs, load_fixed
from pdflow.context import InputError, resolve
from pdflow.patch import apply_patches, apply_toggle, specs_from_fixed, toggles_from_fixed
from pdflow.config import MARKERS_AFTER
from pdflow.precheck import run_precheck
from pdflow.runner import consume_finished
from pdflow.scripts import write_all
from pdflow.summary import append_index, overall, record_result, write_summary
from pdflow.workspace import (create_workspace, now_iso, open_existing, release_lock, update_manifest,
                              write_manifest)

FIXED = load_fixed()
BUILD_CONFIGS = list_build_configs(FIXED)          # dropdown suggestions (validated again at run time)
MODS = FIXED["modules"]
STEP_HELP = "build, init_design, compile, clock_opt_cts, clock_opt_opto, route_auto, route_opt"


def _hard_timeout(step: str):
    """Airflow's own last-resort limit = the largest max_runtime_h + license allowance + stall
    timeout + 2 h (None = no limit). The real limits (stall / max runtime, ACTIVE time only) are
    enforced by the runner, which also stops the remote job."""
    caps = [v for v in (FIXED.get("max_runtime_h") or {}).values() if v]
    if not caps:
        return None
    lic_h = float(FIXED.get("license_env", {}).get("SNPS_MAX_WAITTIME", 0) or 0) / 3600 if step != "build" else 0
    return timedelta(hours=max(caps) + lic_h + float(FIXED.get("stall_timeout_h") or 0) + 2)


PARAMS = {
    "fc_host": Param(
        "", type="string", enum=[""] + FIXED["fc_hosts"], title="pd host (required)",
        values_display={"": "-- choose a pd host --", **{h: h for h in FIXED["fc_hosts"]}},
        description="Where the build and every FC step run (over ssh). precheck logs the current load / "
                    "free RAM of all 5 hosts. mipspd81/82 have 128 cores, mipspd83-85 have 32.",
        section="Where / what"),
    "module": Param(
        "", type="string", enum=[""] + list(MODS), title="Module (required)",
        values_display={"": "-- choose a module --",
                        **{m: f"{m}  (top {c['design_name']}, size {c['size']})" for m, c in MODS.items()}},
        description="Picks DESIGN_NAME, top RTL file, default build config and default clock. "
                    "RESUME: choose the SAME module the run was made with (a different one is refused).",
        section="Where / what"),
    "build_config": Param(
        None, type=["null", "string"], title="Build config (-c / SYN_CONFIG)", examples=BUILD_CONFIGS,
        description="Blank = the module's default (corvi: 2Core_4VC_32K, others: 2Core_4VC_32K_rvn_drive). "
                    "Must be a directory in $MIPS_HOME/raven/src/config.",
        section="Where / what"),
    "clock_period_ns": Param(
        None, type=["null", "number"], title="Clock period (ns)", exclusiveMinimum=0, maximum=20,
        description="Blank = the module's default (corvi 1.25, cic 1.25, debug 2, trf 1.25). Patches "
                    "`set Clk_period` in the run copy's design_setup.tcl (drives every functional clock).",
        section="Knobs"),
    "max_cores": Param(
        16, type="integer", minimum=1, maximum=128, title="Max cores",
        description="set_host_options -max_cores for FC (patched in fc_setup.tcl + design_setup.tcl). "
                    "Must not exceed the chosen host's core count.",
        section="Knobs"),
    "continue_past_s19": Param(
        False, type="boolean", title="Continue past DFT Rule S19",
        description="The RM's compile.tcl EXITS when DFT DRC reports Rule S19 (nonscan cell disturb). ON = the "
                    "run copy only warns and compile continues (the S19 count is shown in the compile gate). "
                    "OFF = RM behaviour (compile stops -> step fails).",
        section="Knobs"),
    "start_from": Param(
        "build", type="string", enum=STEPS, title="Start from step",
        description=f"build = NEW run. Any later step = RESUME an existing run (needs run_tag). Steps: {STEP_HELP}",
        section="Flow control"),
    "stop_at": Param(
        "route_opt", type="string", enum=STEPS, title="Stop after step",
        description="Last step to run (route_opt = the whole flow).", section="Flow control"),
    "run_tag": Param(
        None, type=["null", "string"], title="Run tag", pattern=r"^\s*([A-Za-z0-9][A-Za-z0-9._-]{0,79})?\s*$",   # spaces from copy/paste are stripped
        description="Blank = automatic <module>_<config>_<date-time> (new runs). REQUIRED to resume: the "
                    "existing folder name under /home/jsingh9.scratch/raven_local_ws/.",
        section="Flow control"),
    "gate_after_compile": Param(True, type="boolean", title="Review QoR after compile",
                                description="Pause for Approve/Reject after compile.", section="Review gates"),
    "gate_after_clock_opt": Param(True, type="boolean", title="Review QoR after clock_opt_opto",
                                  description="Pause for Approve/Reject after clock_opt_opto.",
                                  section="Review gates"),
    "dry_run": Param(False, type="boolean", title="Dry run",
                     description="NEW runs only: create the workspace, patches and scripts, run the prechecks -- "
                                 "but launch no tools. Good for checking a setup.", section="Advanced"),
}


@dag(
    dag_id="raven_pd_flow",
    description="Raven module: RTL build -> Fusion Compiler route_opt, with QoR review gates",
    doc_md=__doc__,
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=4,
    default_args={"retries": 0},
    tags=["pd", "fusion_compiler", "raven"],
    params=PARAMS,
)
def raven_pd_flow():

    # cheap, repeatable tasks retry: a transient Airflow hiccup (e.g. API server still starting
    # after a restart) must not fail the run. Tool steps / gates never retry automatically.
    SAFE_RETRY = {"retries": 2, "retry_delay": timedelta(minutes=1)}

    @task(**SAFE_RETRY)
    def resolve_inputs(**context) -> dict:
        now = pendulum.now("local").format("YYYYMMDD-HHmmss")
        try:
            ctx = resolve(context["params"], FIXED, now, context["run_id"], list_build_configs(FIXED))
        except InputError as e:
            raise AirflowFailException("Please fix the trigger form:\n  - " + str(e).replace("\n", "\n  - "))
        print("Resolved run:")
        for k in ("run_tag", "module", "design_name", "build_config", "clk_str", "max_cores", "fc_host",
                  "start_from", "stop_at", "gates", "dry_run", "run_dir"):
            print(f"  {k:14} {ctx[k]}")
        return ctx

    @task(**SAFE_RETRY)
    def precheck(ctx: dict) -> list:
        res = run_precheck(ctx, FIXED, log=print, lock_owner_finished=dag_run_finished)
        for w in res["warnings"]:
            print(f"WARN  {w}")
        for f in res["fatal"]:
            print(f"FAIL  {f}")
        if res["fatal"]:
            raise AirflowFailException(f"precheck: {len(res['fatal'])} problem(s):\n  - " + "\n  - ".join(res["fatal"]))
        print(f"precheck OK ({len(res['warnings'])} warning(s))")
        return res["warnings"]

    @task(**SAFE_RETRY)
    def prepare_workspace(ctx: dict, warnings: list) -> dict:
        run_dir = Path(ctx["run_dir"])
        # a RETRY of this task in the same Airflow run must not trip over its own half-made run dir
        from pdflow.workspace import LOCK
        mine = (run_dir / LOCK).is_file() and ctx["dag_run_id"] in (run_dir / LOCK).read_text()
        if not ctx["is_resume"] and run_dir.exists() and mine and not (run_dir / "run.json").is_file():
            import shutil
            print(f"retry: removing the incomplete run dir from the previous attempt: {run_dir}")
            shutil.rmtree(run_dir)
        hist = {"dag_run_id": ctx["dag_run_id"], "time": now_iso(), "start_from": ctx["start_from"],
                "stop_at": ctx["stop_at"], "fc_host": ctx["fc_host"], "dry_run": ctx["dry_run"],
                "clock_period_ns": ctx["clock_period_ns"], "max_cores": ctx["max_cores"]}
        if not ctx["is_resume"] and (run_dir / "run.json").is_file() and mine:
            print(f"retry: workspace {run_dir} was already created by this Airflow run; re-patching only")
        elif not ctx["is_resume"]:
            create_workspace(ctx, FIXED, log=print)
            snapshot = {k: v for k, v in FIXED.items() if not k.startswith("_")}
            write_manifest(run_dir, {
                "run_tag": ctx["run_tag"], "created": now_iso(), "inputs": ctx,
                "fixed_path": FIXED["_path"], "fixed_sha256": FIXED["_sha256"], "fixed_snapshot": snapshot,
                "local_config": FIXED.get("_local"),
                "precheck_warnings": warnings, "history": [hist],
                "steps": {s: {"status": "pending"} for s in STEPS}, "gates": {}})
        else:
            open_existing(ctx, dag_run_finished)
            todo = STEPS[STEPS.index(ctx["start_from"]): STEPS.index(ctx["stop_at"]) + 1]
            # steps re-run now invalidate everything after them (their nlib labels are re-derived)
            later = [s for s in MARKERS_AFTER.get(ctx["start_from"], []) if s in STEPS and s not in todo]
            for s in todo:
                if consume_finished(run_dir, s):   # an old finished result must not be adopted
                    print(f"{s}: previous result marked consumed -> will run fresh")
            prev_qor = run_dir / "qor" / f".prev-{now_iso().replace(':', '')}"
            for s in todo + later:
                q = run_dir / "qor" / f"{s}.json"
                if q.exists():
                    prev_qor.mkdir(parents=True, exist_ok=True)
                    q.rename(prev_qor / q.name)

            def _f(m):
                m["inputs"] = ctx
                m["precheck_warnings"] = warnings
                m.setdefault("history", []).append(hist)
                for s in todo:
                    m.setdefault("steps", {})[s] = {"status": "pending"}
                for s in later:
                    if m.get("steps", {}).get(s, {}).get("status") in ("ok", "warn"):
                        m["steps"][s]["status"] = "stale"
            update_manifest(run_dir, _f)

        changes = apply_patches(ctx["module_dir"], specs_from_fixed(FIXED),
                                {"clk": ctx["clk_str"], "cores": ctx["max_cores"]},
                                run_dir / "patches" / f"{ctx['module']}.diff")
        for t in toggles_from_fixed(FIXED):
            changes.append(apply_toggle(ctx["module_dir"], t, bool(ctx.get(t.param)),
                                        run_dir / "patches" / f"{ctx['module']}.diff"))
        for c in changes:
            print(f"patch {c['id']}: {c['before'].strip()!r} -> {c['after'].strip()!r}  ({c['file']})")
        scripts = write_all(ctx, FIXED)
        print(f"scripts in {run_dir / 'scripts'}: {', '.join(Path(p).name for p in scripts.values())}")
        update_manifest(run_dir, lambda m: m.update({"patches": changes, "scripts": scripts}))
        return {"run_dir": str(run_dir)}

    @task(trigger_rule="all_done", **SAFE_RETRY)
    def finalize(**context):
        ti = context["ti"]
        ctx = ti.xcom_pull(task_ids="resolve_inputs")
        if not ctx:
            raise AirflowFailException("run failed before its inputs were resolved (see resolve_inputs)")
        run_dir = Path(ctx["run_dir"])
        from pdflow.workspace import read_manifest
        m = read_manifest(run_dir) if (run_dir / "run.json").is_file() else {}
        # grade ONLY a run dir this DAG run actually opened (prepare_workspace pushed its XCom and
        # appended our history entry); otherwise a failed precheck could grade an old run as PASS
        if ti.xcom_pull(task_ids="prepare_workspace") is None or \
                not any(h.get("dag_run_id") == ctx["dag_run_id"] for h in m.get("history", [])):
            where = f"partial workspace left at {run_dir}" if run_dir.exists() else f"{run_dir} not created"
            raise AirflowFailException(f"run failed before/at precheck or prepare_workspace ({where}); "
                                       f"see those task logs")
        ok, probs = overall(ctx, m)
        record_result(ctx, ok, probs)
        out = write_summary(ctx, ok, probs)
        append_index(FIXED, ctx, ok, probs)
        release_lock(run_dir, ctx["dag_run_id"])
        print(out.read_text())
        if not ok:
            raise AirflowFailException("run FAILED:\n  - " + "\n  - ".join(probs))
        print(f"run PASSED: {out}")

    ctx = resolve_inputs()
    warns = precheck(ctx)
    ws = prepare_workspace(ctx, warns)

    # Tool steps retry only for Airflow/ssh/host trouble (a retry re-attaches to the still-running
    # job or adopts its result). A real flow failure (checks) raises AirflowFailException = no retry.
    STEP_RETRY = {"retries": 3, "retry_delay": timedelta(minutes=2), "retry_exponential_backoff": True,
                  "max_retry_delay": timedelta(minutes=15)}
    build = PdStepOperator(task_id="build_rtl", step="build", pool="pd_build",
                           execution_timeout=_hard_timeout("build"), **STEP_RETRY)
    fc = {s: PdStepOperator(task_id=f"fc_{s}", step=s, pool="pd_fc", execution_timeout=_hard_timeout(s),
                            **STEP_RETRY) for s in FC_STEPS}
    gate_compile = PdQorGate(task_id="gate_compile", gated_step="compile", retries=0,
                             response_hours=FIXED["hitl_response_timeout_h"])
    gate_clock = PdQorGate(task_id="gate_clock_opt", gated_step="clock_opt_opto", retries=0,
                           response_hours=FIXED["hitl_response_timeout_h"])
    fin = finalize()

    ws >> build >> fc["init_design"] >> fc["compile"] >> gate_compile >> fc["clock_opt_cts"] \
        >> fc["clock_opt_opto"] >> gate_clock >> fc["route_auto"] >> fc["route_opt"] >> fin
    # finalize must also run (all_done) when the flow dies before build_rtl
    [ctx, warns, ws] >> fin


raven_pd_flow()
