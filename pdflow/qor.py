"""Parse FC RM reports (rpts/<step>/<step>.*) into numbers + render the gate's Markdown."""
from __future__ import annotations

import re
from pathlib import Path

NUM = r"(-?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?)"


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_global_timing(text: str) -> dict:
    """report_global_timing (narrow): {'setup': {wns,tns,num}, 'hold': {...}}; 'Total' column."""
    out = {}
    for kind in ("setup", "hold"):
        if re.search(rf"No {kind} violations found", text, re.I):
            out[kind] = {"wns": 0.0, "tns": 0.0, "num": 0}
            continue
        m = re.search(rf"^{kind.capitalize()} violations\s*$(.*?)(?=^\w[\w ]* violations\s*$|\Z)",
                      text, re.M | re.S)
        if not m:
            continue
        sec = m.group(1)
        d = {}
        for key in ("WNS", "TNS", "NUM"):
            r = re.search(rf"^\s*{key}\s+{NUM}", sec, re.M)
            if r:
                d[key.lower()] = int(float(r.group(1))) if key == "NUM" else float(r.group(1))
        if d:
            out[kind] = d
    return out


def parse_report_qor(text: str) -> dict:
    groups = []
    for m in re.finditer(r"^Scenario\s+'([^']+)'\s*\nTiming Path Group\s+'([^']+)'\s*\n-+\n(.*?)\n-+",
                         text, re.M | re.S):
        body = m.group(3)

        def g(label):
            r = re.search(rf"^{re.escape(label)}:\s+{NUM}", body, re.M)
            return _f(r.group(1)) if r else None
        groups.append({"scenario": m.group(1), "group": m.group(2),
                       "levels": g("Levels of Logic"), "slack": g("Critical Path Slack"),
                       "clk_period": g("Critical Path Clk Period"), "tns": g("Total Negative Slack"),
                       "nvp": g("No. of Violating Paths"), "hold_wns": g("Worst Hold Violation"),
                       "hold_tns": g("Total Hold Violation"), "hold_num": g("No. of Hold Violations")})

    def top(label):
        r = re.search(rf"^{re.escape(label)}:?\s+{NUM}", text, re.M)
        return _f(r.group(1)) if r else None
    return {"groups": groups, "leaf_cells": top("Leaf Cell Count"), "cell_area": top("Cell Area (netlist)"),
            "max_trans": top("Max Trans Violations"), "max_cap": top("Max Cap Violations"),
            "nets_with_violations": top("Nets with Violations")}


def parse_utilization(text: str) -> float | None:
    r = re.search(rf"Utilization Ratio:\s*{NUM}", text)
    return _f(r.group(1)) if r else None


def parse_drc(text: str) -> int | None:
    r = re.search(r"Total number of DRCs\s*=\s*(\d+)", text)
    return int(r.group(1)) if r else None


def parse_open_nets(text: str) -> int | None:
    r = re.search(r"Total number of open nets\s*=\s*(\d+)", text)
    return int(r.group(1)) if r else None


def _read(p: Path) -> str | None:
    try:
        return p.read_text(errors="replace")
    except (FileNotFoundError, OSError):
        return None


def summarize_step(rpt_dir, step: str) -> dict:
    """Numbers for one step; missing reports -> keys absent + listed in 'missing'."""
    rpt_dir = Path(rpt_dir)
    s: dict = {"step": step, "missing": []}
    gt = _read(rpt_dir / f"{step}.report_global_timing")
    if gt is None:
        s["missing"].append(f"{step}.report_global_timing")
    else:
        s["global_timing"] = parse_global_timing(gt)
    q = _read(rpt_dir / f"{step}.report_qor")
    if q is None:
        s["missing"].append(f"{step}.report_qor")
    else:
        s["qor"] = parse_report_qor(q)
    u = _read(rpt_dir / f"{step}.report_utilization")
    if u is not None:
        s["utilization"] = parse_utilization(u)
    cr = _read(rpt_dir / f"{step}.check_routes")
    if cr is not None:
        s["drc"] = parse_drc(cr)
        s["open_nets"] = parse_open_nets(cr)
    return s


def worst_setup(summary: dict) -> dict:
    gt = summary.get("global_timing", {}).get("setup")
    if gt:
        return gt
    groups = summary.get("qor", {}).get("groups", [])
    sl = [g for g in groups if g.get("slack") is not None]
    if not sl:
        return {}
    w = min(sl, key=lambda g: g["slack"])
    return {"wns": min(0.0, w["slack"]), "tns": sum(g.get("tns") or 0 for g in groups),
            "num": int(sum(g.get("nvp") or 0 for g in groups))}


def _fmt(v, nd=3):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def render_markdown(ctx: dict, gated_step: str, summaries: dict, step_state: dict, extra: list[str]) -> str:
    """Gate body. summaries = {step: summarize_step(...)} for the steps done so far."""
    cols = [s for s in ("compile", "clock_opt_cts", "clock_opt_opto") if s in summaries]
    L = [f"### {ctx['module']} — `{ctx['run_tag']}` — review after **{gated_step}**", "",
         f"- design **{ctx['design_name']}**, build config `{ctx['build_config']}`, host `{ctx['fc_host']}`",
         f"- requested clock **{ctx['clk_str']} ns**, max_cores **{ctx['max_cores']}**", ""]
    facts = step_state.get("facts", {})
    if facts.get("clk_echo") is not None or facts.get("report_clk_period") is not None:
        L.append(f"- confirmed: FC log `set Clk_period {facts.get('clk_echo', '–')}`, "
                 f"report_qor Clk period {facts.get('report_clk_period', '–')}, "
                 f"`-max_cores {facts.get('cores_echo', '–')}`")
    if step_state.get("duration_s"):
        L.append(f"- {gated_step} runtime: {step_state['duration_s'] / 3600:.2f} h")
    L += ["", "| metric | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]

    def row(name, fn):
        L.append(f"| {name} | " + " | ".join(_fmt(fn(summaries[c])) for c in cols) + " |")
    row("setup WNS", lambda s: worst_setup(s).get("wns"))
    row("setup TNS", lambda s: worst_setup(s).get("tns"))
    row("setup violating paths", lambda s: worst_setup(s).get("num"))
    row("hold WNS", lambda s: s.get("global_timing", {}).get("hold", {}).get("wns"))
    row("hold TNS", lambda s: s.get("global_timing", {}).get("hold", {}).get("tns"))
    row("utilization", lambda s: s.get("utilization"))
    row("cell area", lambda s: s.get("qor", {}).get("cell_area"))
    row("leaf cells", lambda s: s.get("qor", {}).get("leaf_cells"))
    row("max trans / cap viol", lambda s: f"{_fmt(s.get('qor', {}).get('max_trans'), 0)} / "
                                          f"{_fmt(s.get('qor', {}).get('max_cap'), 0)}")

    cur = summaries.get(gated_step, {})
    groups = sorted([g for g in cur.get("qor", {}).get("groups", []) if g.get("slack") is not None],
                    key=lambda g: g["slack"])[:5]
    if groups:
        L += ["", f"**Worst path groups ({gated_step})**", "",
              "| scenario | group | slack | TNS | NVP | clk period |", "|---|---|---|---|---|---|"]
        L += [f"| {g['scenario']} | {g['group']} | {_fmt(g['slack'])} | {_fmt(g['tns'])} | "
              f"{_fmt(g['nvp'], 0)} | {_fmt(g['clk_period'])} |" for g in groups]
    if cur.get("missing"):
        L += ["", "Missing reports: " + ", ".join(cur["missing"])]
    warns = step_state.get("warnings", []) + extra
    if warns:
        L += ["", "**Warnings**", ""] + [f"- {w}" for w in warns[:25]]
    L += ["", f"Reports: `{ctx['module_dir']}/rm_fc/rpts/{gated_step}/`  ",
          f"Log: `{ctx['module_dir']}/rm_fc/logs/{gated_step}.log`", "",
          "**Approve** = continue with the next step. **Reject** = stop this run (it ends failed; "
          f"you can resume later with run_tag `{ctx['run_tag']}` and a later start_from)."]
    return "\n".join(L)
