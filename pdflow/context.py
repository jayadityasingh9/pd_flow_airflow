"""Resolve the trigger-form inputs into one run context (a plain JSON-able dict).

Nothing here touches the disk except reading run.json on resume, so it is safe as the first
task and easy to unit-test.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import GATES, STEPS, module_cfg, step_index

TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
# The modules' FC read scripts do `grep -v sim_model | grep -v verilog` on the build's filelist
# (to drop sim models + the std-cell .v). Any run path containing these words would silently
# drop EVERY RTL file, so such tags/roots are refused.
FORBIDDEN_IN_PATH = ("verilog", "sim_model")
CLK_MIN, CLK_MAX = 0.05, 20.0


class InputError(ValueError):
    """A trigger-form problem the user must fix (reported verbatim, no retry)."""


def fmt_clk(x: float) -> str:
    """1.25 -> '1.25', 2.0 -> '2' (what gets written into design_setup.tcl)."""
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _manifest_path(runs_root: str, tag: str) -> Path:
    return Path(runs_root) / tag / "run.json"


def resolve(params: dict, fixed: dict, now: str, dag_run_id: str, build_configs: list[str] | None = None) -> dict:
    """params = the trigger form; now = 'YYYYMMDD-HHMMSS' (for auto tags). Raises InputError."""
    p = {k: (v.strip() if isinstance(v, str) else v) for k, v in (params or {}).items()}
    errs: list[str] = []

    start = p.get("start_from") or "build"
    stop = p.get("stop_at") or "route_opt"
    for name, s in (("start_from", start), ("stop_at", stop)):
        if s not in STEPS:
            errs.append(f"{name}={s!r} is not a step ({', '.join(STEPS)})")
    if not errs and step_index(start) > step_index(stop):
        errs.append(f"start_from={start} comes after stop_at={stop}")

    host = p.get("fc_host") or ""
    if host not in fixed["fc_hosts"]:
        errs.append(f"fc_host is required: choose one of {', '.join(fixed['fc_hosts'])}"
                    + (f" (got {host!r})" if host else ""))

    try:
        cores = int(p.get("max_cores") if not _blank(p.get("max_cores")) else 16)
        if not 1 <= cores <= 128:
            errs.append(f"max_cores={cores} must be 1..128")
    except (TypeError, ValueError):
        errs.append(f"max_cores={p.get('max_cores')!r} is not an integer")
        cores = 16

    dry = bool(p.get("dry_run", False))
    tag = p.get("run_tag") or ""
    is_resume = start != "build"
    prev: dict = {}

    if tag and not TAG_RE.match(tag):
        errs.append(f"run_tag={tag!r} must match {TAG_RE.pattern}")
    for bad in FORBIDDEN_IN_PATH:
        if bad in tag.lower() or bad in fixed["runs_root"].lower():
            errs.append(f"run_tag/runs_root must not contain {bad!r} (the FC read script greps it out of the filelist)")

    if is_resume:
        if not tag:
            errs.append(f"start_from={start} means RESUME: run_tag of the existing run is required")
        else:
            mp = _manifest_path(fixed["runs_root"], tag)
            if not mp.is_file():
                errs.append(f"resume: {mp} not found (start_from=build creates a new run)")
            else:
                prev = json.loads(mp.read_text()).get("inputs", {})

    # --- module / config / clock: form value, else previous run (resume), else module default
    module = p.get("module") or prev.get("module") or ""
    if not module:
        errs.append(f"module is required: choose one of {', '.join(fixed['modules'])}")
    elif prev and module != prev.get("module"):
        errs.append(f"resume: run {tag} is {prev.get('module')}, not {module} (leave module blank or match it)")
    if errs and not module:
        raise InputError("\n".join(errs))
    try:
        mcfg = module_cfg(fixed, module)
    except ValueError as e:
        raise InputError("\n".join(errs + [str(e)])) from None

    cfg = p.get("build_config") or prev.get("build_config") or mcfg["default_build_config"]
    if prev and cfg != prev.get("build_config"):
        errs.append(f"resume: run {tag} was built with {prev.get('build_config')}, not {cfg}")
    if not re.match(r"^[A-Za-z0-9_]+$", cfg):
        errs.append(f"build_config={cfg!r}: only letters, digits, _")
    elif build_configs is not None and build_configs and cfg not in build_configs:
        errs.append(f"build_config={cfg!r} is not a directory in {fixed['build_configs_dir']}")

    if _blank(p.get("clock_period_ns")):
        clk = float(prev.get("clock_period_ns") or mcfg["default_clk_period"])
    else:
        try:
            clk = float(p["clock_period_ns"])
        except (TypeError, ValueError):
            errs.append(f"clock_period_ns={p['clock_period_ns']!r} is not a number")
            clk = float(mcfg["default_clk_period"])
    clk = float(fmt_clk(clk))          # exactly what gets written into design_setup.tcl
    if not CLK_MIN <= clk <= CLK_MAX:
        errs.append(f"clock_period_ns={clk} must be {CLK_MIN}..{CLK_MAX} ns")
    if prev and abs(clk - float(prev.get("clock_period_ns", clk))) > 1e-9 and start != "init_design":
        errs.append(f"resume: clock {prev.get('clock_period_ns')} -> {clk} ns only takes effect from "
                    f"init_design (the SDC is read there); use start_from=init_design")

    # --- technology node: form value, else the run's (resume), else the default; must exist for the module
    nodes = fixed.get("tech_nodes") or {fixed["tech_dir"]: {"build_tech": fixed["build_tech"]}}
    tech = p.get("tech_node") or prev.get("tech_dir") or fixed["tech_dir"]
    if tech not in nodes:
        errs.append(f"tech_node={tech!r} is not configured (config tech_nodes: {', '.join(nodes)})")
    elif prev and tech != prev.get("tech_dir", fixed["tech_dir"]):
        errs.append(f"resume: run {tag} uses technology {prev.get('tech_dir')}, not {tech} (a different tech "
                    f"node needs a NEW run: start_from=build)")
    elif not (Path(fixed["rm_fc_cad_src"]) / module / "block_files" / tech).is_dir():
        have = sorted(d.name for d in (Path(fixed["rm_fc_cad_src"]) / module / "block_files").iterdir()
                      if d.is_dir() and d.name not in ("common", "utils"))
        errs.append(f"{module} has no block_files/{tech} (available for {module}: {', '.join(have)})")
    build_tech = (nodes.get(tech) or {}).get("build_tech", fixed["build_tech"])

    # --- max low-VT %: form value, else the run's (resume), else the RM default
    if _blank(p.get("max_low_vt_pct")):
        low_vt = int(prev.get("max_low_vt_pct") if prev.get("max_low_vt_pct") is not None else 10)
    else:
        try:
            low_vt = int(p["max_low_vt_pct"])
        except (TypeError, ValueError):
            errs.append(f"max_low_vt_pct={p['max_low_vt_pct']!r} is not an integer")
            low_vt = 10
    if not 0 <= low_vt <= 100:
        errs.append(f"max_low_vt_pct={low_vt} must be 0..100")
    if prev and prev.get("max_low_vt_pct") is not None and low_vt != int(prev["max_low_vt_pct"]) \
            and step_index(start) > step_index("compile"):
        errs.append(f"resume: max_low_vt_pct {prev['max_low_vt_pct']} -> {low_vt} only takes effect from compile "
                    f"(synthesis); use start_from=compile or earlier")

    if dry and is_resume:
        errs.append("dry_run is for NEW runs only (a dry resume would rewrite the existing run's state)")

    if not tag:
        short = module.replace("_module", "")
        tag = f"{'dry-' if dry else ''}{short}_{cfg}_{now}"

    if errs:
        raise InputError("\n".join(errs))

    runs_root = Path(fixed["runs_root"])
    run_dir = runs_root / tag
    rc_dir = run_dir / "rm_fc_cad"
    return {
        "run_tag": tag,
        "run_dir": str(run_dir),
        "rc_dir": str(rc_dir),
        "module": module,
        "module_dir": str(rc_dir / module),
        "design_name": mcfg["design_name"],
        "top_file": str(Path(fixed["mips_home"]) / mcfg["top_file"]),
        "build_config": cfg,
        "clock_period_ns": clk,
        "clk_str": fmt_clk(clk),
        "max_cores": cores,
        "start_from": start,
        "stop_at": stop,
        "gates": {step: bool(p.get(flag, True)) for step, flag in GATES.items()},
        "dry_run": dry,
        "continue_past_s19": bool(p.get("continue_past_s19", False)),
        "fc_host": host,
        "is_resume": is_resume,
        "dag_run_id": dag_run_id,
        "size": mcfg.get("size", "L"),
        "tech_dir": tech,
        "build_tech": build_tech,
        "max_low_vt_pct": low_vt,
        "created": now,
    }


def step_mode(ctx: dict, step: str) -> str:
    """'reuse' (before start_from: verify only), 'skip' (after stop_at), 'dry', or 'run'."""
    i = step_index(step)
    if i < step_index(ctx["start_from"]):
        return "reuse"
    if i > step_index(ctx["stop_at"]):
        return "skip"
    return "dry" if ctx["dry_run"] else "run"


def gate_needed(ctx: dict, gated_step: str) -> tuple[bool, str]:
    """Does the approval gate after `gated_step` actually wait for a human in this run?"""
    i = step_index(gated_step)
    if not ctx["gates"].get(gated_step, True):
        return False, f"gate after {gated_step} switched off in the trigger form"
    if ctx["dry_run"]:
        return False, "dry run"
    if i < step_index(ctx["start_from"]):
        return False, (f"{gated_step} was not re-run (resumed with start_from={ctx['start_from']}, i.e. past "
                       f"this gate); see run.json history for the earlier decision")
    if i >= step_index(ctx["stop_at"]):
        return False, f"nothing runs after {gated_step} (stop_at={ctx['stop_at']})"
    return True, "waiting for review"
