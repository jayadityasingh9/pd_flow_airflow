"""Fixed configuration (config/fixed.yaml) and the step vocabulary."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import yaml

# Order matters: this IS the flow. `setup` and `place_opt` are marker-only (UNIFIED_FLOW=true)
# and are written by the step scripts, so they are not Airflow tasks.
STEPS = ["build", "init_design", "compile", "clock_opt_cts", "clock_opt_opto", "route_auto", "route_opt"]
FC_STEPS = STEPS[1:]
# Steps whose rpts/<step>/ has report_qor output (init_design in RTL mode skips report_qor).
QOR_STEPS = ["compile", "clock_opt_cts", "clock_opt_opto", "route_auto", "route_opt"]
# Gated step -> the trigger-form boolean that enables its approval gate.
GATES = {"compile": "gate_after_compile", "clock_opt_opto": "gate_after_clock_opt"}
# Markers each FC step must delete before it runs (itself + everything after it), so a stale
# marker can never make a failed re-run look finished.
MARKERS_AFTER = {
    "init_design":    ["init_design", "compile", "place_opt", "clock_opt_cts", "clock_opt_opto", "route_auto", "route_opt"],
    "compile":        ["compile", "place_opt", "clock_opt_cts", "clock_opt_opto", "route_auto", "route_opt"],
    "clock_opt_cts":  ["clock_opt_cts", "clock_opt_opto", "route_auto", "route_opt"],
    "clock_opt_opto": ["clock_opt_opto", "route_auto", "route_opt"],
    "route_auto":     ["route_auto", "route_opt"],
    "route_opt":      ["route_opt"],
}

DEFAULT_FIXED = Path(__file__).resolve().parent.parent / "config" / "fixed.yaml"


def step_index(step: str) -> int:
    try:
        return STEPS.index(step)
    except ValueError:
        raise ValueError(f"unknown step {step!r}; valid: {', '.join(STEPS)}") from None


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    if v:
        return v
    if name in ("USER", "LOGNAME"):
        import getpass
        return getpass.getuser()
    if name == "HOME":
        return os.path.expanduser("~")
    return default


def _expand(value, scalars: dict):
    """${key}: another top-level scalar of the config first, else an environment variable
    (${USER}, ${HOME}, ...); unknown names are left as they are."""
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}",
                      lambda m: str(scalars[m.group(1)]) if m.group(1) in scalars
                      else _env(m.group(1), m.group(0)), value)
    if isinstance(value, list):
        return [_expand(v, scalars) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, scalars) for k, v in value.items()}
    return value


def _merge(base: dict, over: dict) -> dict:
    """Overlay `over` on `base`: nested dicts merge key by key, everything else replaces."""
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def local_config_path(fixed_path: Path) -> Path:
    """The per-user overlay, first match wins: $PDFLOW_LOCAL; ~/.pdflow/local.yaml (a user of a SHARED
    checkout, created by bin/pdflow-user-setup.sh); config/local.yaml next to fixed.yaml (own clone)."""
    if os.environ.get("PDFLOW_LOCAL"):
        return Path(os.environ["PDFLOW_LOCAL"])
    user = Path(os.path.expanduser("~")) / ".pdflow" / "local.yaml"
    return user if user.is_file() else fixed_path.with_name("local.yaml")


def load_fixed(path: str | os.PathLike | None = None) -> dict:
    """Load fixed.yaml (+ the optional per-user config/local.yaml on top), expand ${key}
    references (config scalars, then environment variables), add _path/_sha256/_local."""
    path = Path(path or os.environ.get("PDFLOW_FIXED") or DEFAULT_FIXED)
    text = path.read_text()
    raw = yaml.safe_load(text)
    lp = local_config_path(path)
    local_text = lp.read_text() if lp.is_file() else ""
    if local_text.strip():
        raw = _merge(raw, yaml.safe_load(local_text) or {})
    # Expand scalars against each other first (a few passes resolves chains like rm_fc_cad_src).
    scalars = {k: v for k, v in raw.items() if isinstance(v, (str, int, float))}
    for _ in range(5):
        scalars = {k: _expand(v, scalars) for k, v in scalars.items()}
    cfg = _expand(raw, scalars)
    cfg["_path"] = str(path)
    cfg["_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    cfg["_local"] = {"path": str(lp), "sha256": hashlib.sha256(local_text.encode()).hexdigest()} if local_text else None
    return cfg


def list_build_configs(fixed: dict) -> list[str]:
    """The valid -c/--configName values (directories). Never raises: [] if unreadable."""
    try:
        d = Path(fixed["build_configs_dir"])
        return sorted(p.name for p in d.iterdir() if p.is_dir())
    except OSError:
        return []


def module_cfg(fixed: dict, module: str) -> dict:
    try:
        return fixed["modules"][module]
    except KeyError:
        raise ValueError(f"unknown module {module!r}; valid: {', '.join(fixed['modules'])}") from None
