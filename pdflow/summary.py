"""End-of-run bookkeeping: summary.md, runs_index.tsv, overall verdict (used by `finalize`)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import STEPS, step_index
from .qor import worst_setup
from .workspace import read_manifest, update_manifest

INDEX_HDR = "time\trun_tag\tmodule\tbuild_config\tclk_ns\tcores\thost\tstart_from\tstop_at\tresult\tdetail\n"


def overall(ctx: dict, manifest: dict) -> tuple[bool, list[str]]:
    """(ok, problems): every step in [start_from..stop_at] must be ok/warn (dry in a dry run)."""
    good = ("dry", "ok", "warn") if ctx["dry_run"] else ("ok", "warn")
    probs = []
    for s in STEPS[step_index(ctx["start_from"]): step_index(ctx["stop_at"]) + 1]:
        st = manifest.get("steps", {}).get(s, {})
        status = st.get("status", "missing")
        if status not in good:
            why = "; ".join(st.get("fatal", [])[:2]) or ("not run (an earlier step failed or a gate rejected)"
                                                         if status in ("pending", "missing") else "")
            probs.append(f"{s}: {status}" + (f" -- {why}" if why else ""))
    for g, d in manifest.get("gates", {}).items():
        if d.get("decision") in ("rejected", "timeout") and d.get("dag_run_id") == ctx["dag_run_id"]:
            probs.insert(0, f"gate after {g}: {d['decision']} by {d.get('user', '?')}"
                            + (f" ({d['comment']})" if d.get("comment") else ""))
    return (not probs), probs


def write_summary(ctx: dict, ok: bool, probs: list[str]) -> Path:
    run_dir = Path(ctx["run_dir"])
    m = read_manifest(run_dir)
    L = [f"# {ctx['run_tag']} — {'PASS' if ok else 'FAIL'}", "",
         f"- module **{ctx['module']}** ({ctx['design_name']}), build config `{ctx['build_config']}`",
         f"- Clk_period {ctx['clk_str']} ns, max_cores {ctx['max_cores']}, host {ctx['fc_host']}",
         f"- steps {ctx['start_from']} → {ctx['stop_at']}{'  (DRY RUN)' if ctx['dry_run'] else ''}",
         f"- Airflow run `{ctx['dag_run_id']}`, finished {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    if probs:
        L += ["## Problems", ""] + [f"- {p}" for p in probs] + [""]
    L += ["## Steps", "", "| step | status | host | runtime | setup WNS | TNS | hold WNS | util | warnings |",
          "|---|---|---|---|---|---|---|---|---|"]
    for s in STEPS:
        st = m.get("steps", {}).get(s, {})
        q = {}
        qf = run_dir / "qor" / f"{s}.json"
        if qf.is_file() and st.get("status") in ("ok", "warn"):
            q = json.loads(qf.read_text())
        ws = worst_setup(q) if q else {}
        hold = q.get("global_timing", {}).get("hold", {}) if q else {}
        dur = st.get("duration_s")
        L.append(f"| {s} | {st.get('status', '–')} | {st.get('host', '–')} | "
                 f"{f'{dur / 3600:.2f} h' if dur else '–'} | {ws.get('wns', '–')} | {ws.get('tns', '–')} | "
                 f"{hold.get('wns', '–')} | {q.get('utilization', '–') if q else '–'} | {len(st.get('warnings', []))} |")
    if m.get("gates"):
        L += ["", "## Gates", ""] + [f"- after {g}: {d.get('decision')} by {d.get('user', '–')} at {d.get('time', '–')}"
                                     + (f" — {d['comment']}" if d.get("comment") else "")
                                     for g, d in m["gates"].items()]
    L += ["", "## Where things are", "",
          f"- run dir: `{run_dir}`", f"- scripts (re-runnable by hand): `{run_dir}/scripts/`",
          f"- FC logs: `{ctx['module_dir']}/rm_fc/logs/`", f"- FC reports: `{ctx['module_dir']}/rm_fc/rpts/`",
          f"- build: `{ctx['rc_dir']}/vbuild/{ctx['build_config']}/build.log`",
          f"- patches applied: `{run_dir}/patches/{ctx['module']}.diff`", ""]
    out = run_dir / "summary.md"
    out.write_text("\n".join(L))
    return out


def append_index(fixed: dict, ctx: dict, ok: bool, probs: list[str]) -> None:
    idx = Path(fixed["runs_root"]) / "runs_index.tsv"
    new = not idx.exists()
    with open(idx, "a") as f:
        if new:
            f.write(INDEX_HDR)
        f.write("\t".join([time.strftime("%Y-%m-%d %H:%M:%S"), ctx["run_tag"], ctx["module"], ctx["build_config"],
                           ctx["clk_str"], str(ctx["max_cores"]), ctx["fc_host"], ctx["start_from"], ctx["stop_at"],
                           ("DRY-" if ctx["dry_run"] else "") + ("PASS" if ok else "FAIL"),
                           (probs[0] if probs else "").replace("\t", " ")[:200]]) + "\n")


def record_result(ctx: dict, ok: bool, probs: list[str]) -> None:
    def _f(m):
        m.setdefault("history", [])
        for h in reversed(m["history"]):
            if h.get("dag_run_id") == ctx["dag_run_id"]:
                h["result"] = "PASS" if ok else "FAIL"
                h["problems"] = probs
                h["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                break
    update_manifest(ctx["run_dir"], _f)
