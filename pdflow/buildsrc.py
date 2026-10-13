"""Where the RTL comes from: a fresh build.py run, an existing build, or a user filelist.

The module's RM read script (block_files/common/<DESIGN>.FC.read_design.tcl) reads, in order:
  1. $MIPS_HOME/../filelists/<module>_syn_rtl.tcl      (an override, if the file exists)
  2. ../../../vbuild/$SYN_CONFIG/vbuild/py.Work/synthesis_{inc,rtl,pkg}.f   (a build's output)
Both live under the run's own rm_fc_cad copy, so "reuse a build" = copy that build's py.Work
into the run, and "custom filelist" = install the user's Tcl filelist as the override. Neither
touches Perforce.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

BUILD_SOURCES = ("build", "existing", "filelist")
PYWORK_FILES = ("synthesis_rtl.f", "synthesis_inc.f", "synthesis_pkg.f")


class BuildSourceError(ValueError):
    pass


def build_tech_of(pywork: Path) -> str | None:
    """The -tech a build was made with, from its build.info (two levels above py.Work)."""
    info = pywork.parent.parent / "build.info"
    try:
        m = re.search(r"-tech\s+(\S+)", info.read_text(errors="replace"))
        return m.group(1) if m else None
    except OSError:
        return None


def find_pywork(path: str) -> Path:
    """Accept a run dir, a vbuild/<cfg> dir, a vbuild/<cfg>/vbuild dir or the py.Work dir itself."""
    p = Path(path).expanduser()
    if not p.exists():
        raise BuildSourceError(f"build path does not exist: {p}")
    cands = [p, p / "vbuild" / "py.Work", p / "py.Work"]
    if (p / "run.json").is_file():                      # a run dir: use its build config
        try:
            cfg = json.loads((p / "run.json").read_text())["inputs"]["build_config"]
            cands.insert(0, p / "rm_fc_cad" / "vbuild" / cfg / "vbuild" / "py.Work")
        except (KeyError, ValueError, OSError):
            pass
    for c in cands:
        if (c / "synthesis_rtl.f").is_file():
            return c
    raise BuildSourceError(f"no synthesis_rtl.f under {p} (give a run dir, a vbuild/<config> dir or a py.Work dir)")


def _tcl_list(text: str, var: str) -> list[str]:
    """Values of `set VAR "a b \\ c"` / `set VAR {a b}` in an RM-style filelist."""
    m = re.search(rf'^\s*set\s+{var}\s+(?:"((?:[^"\\]|\\.|\\\n)*)"|\{{([^}}]*)\}}|(\S+))', text, re.M | re.S)
    if not m:
        return []
    body = m.group(1) if m.group(1) is not None else (m.group(2) if m.group(2) is not None else m.group(3))
    return [t for t in re.split(r"[\s\\]+", body) if t and not t.startswith("$")]


def check_existing(path: str, want_build_tech: str) -> dict:
    """Validate a build to reuse. Returns {'pywork', 'build_tech', 'rtl_files', 'missing'}; raises."""
    pw = find_pywork(path)
    probs = []
    for f in PYWORK_FILES:
        if not (pw / f).is_file() or (pw / f).stat().st_size == 0:
            probs.append(f"{f} missing or empty in {pw}")
    tech = build_tech_of(pw)
    if tech is None:
        probs.append(f"cannot read the build's -tech from {pw.parent.parent / 'build.info'}")
    elif tech != want_build_tech:
        probs.append(f"the build was made with -tech {tech}, but the chosen technology node needs {want_build_tech} "
                     f"(a build is tied to its tech: run a new build instead)")
    rtl = _tcl_list((pw / "synthesis_rtl.f").read_text(errors="replace"), "RTL_SOURCE_FILES") if (pw / "synthesis_rtl.f").is_file() else []
    missing = [f for f in rtl if not Path(f).exists()]
    if missing:
        probs.append(f"{len(missing)} of {len(rtl)} RTL files listed by that build no longer exist (first: {missing[0]})")
    if probs:
        raise BuildSourceError("; ".join(probs))
    return {"pywork": str(pw), "build_tech": tech, "rtl_files": len(rtl), "missing": 0}


def check_filelist(path: str) -> dict:
    """Validate a user RM-style Tcl filelist (set RTL_SOURCE_FILES / INC_DIRS [/ RTL_PACKAGE_SOURCE_FILES])."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise BuildSourceError(f"filelist does not exist: {p}")
    text = p.read_text(errors="replace")
    rtl = _tcl_list(text, "RTL_SOURCE_FILES")
    inc = _tcl_list(text, "INC_DIRS")
    pkg = _tcl_list(text, "RTL_PACKAGE_SOURCE_FILES")
    if not rtl:
        raise BuildSourceError(f"{p}: no `set RTL_SOURCE_FILES ...` found (RM Tcl filelist format expected: "
                               f"set RTL_SOURCE_FILES \"a.sv b.sv\"; set INC_DIRS \"dir1 dir2\")")
    rel = [f for f in rtl + pkg if not f.startswith("/")]
    if rel:
        raise BuildSourceError(f"{p}: {len(rel)} RTL paths are relative (first: {rel[0]}); use absolute paths")
    missing = [f for f in rtl + pkg + inc if not Path(f).exists()]
    if missing:
        raise BuildSourceError(f"{p}: {len(missing)} listed paths do not exist (first: {missing[0]})")
    warn = []
    if pkg:
        # the RM override branch analyzes only $RTL_SOURCE_FILES
        warn.append(f"{len(pkg)} RTL_PACKAGE_SOURCE_FILES are ignored by the RM read script's override branch: "
                    f"list packages FIRST inside RTL_SOURCE_FILES instead")
    return {"filelist": str(p), "rtl_files": len(rtl), "inc_dirs": len(inc), "pkg_files": len(pkg), "warnings": warn}


def install_existing(ctx: dict, pywork: str, log=print) -> Path:
    """Copy a build's py.Work (3 synthesis_*.f) into this run where the read script expects it."""
    dst = Path(ctx["rc_dir"]) / "vbuild" / ctx["build_config"] / "vbuild" / "py.Work"
    dst.mkdir(parents=True, exist_ok=True)
    for f in PYWORK_FILES:
        shutil.copy2(Path(pywork) / f, dst / f)
    src_info = Path(pywork).parent.parent / "build.info"
    if src_info.is_file():
        shutil.copy2(src_info, dst.parent.parent / "build.info")
    (dst / "PDFLOW_REUSED_FROM").write_text(f"{pywork}\n")
    log(f"build reused: {pywork} -> {dst}")
    return dst


def install_filelist(ctx: dict, fixed: dict, filelist: str, log=print) -> Path:
    """Install the user's Tcl filelist: the RM read script's first branch sources
    $MIPS_HOME/../filelists/<override> -- a path in the Perforce tree, which we never touch. So the
    RUN COPY of the read script is rewritten to source <run>/user_filelist.tcl instead (same branch,
    same `analyze` of $RTL_SOURCE_FILES with $INC_DIRS on the search path)."""
    mod = Path(ctx["module_dir"])
    read = mod / "block_files" / "common" / f"{ctx['design_name']}.FC.read_design.tcl"
    dst = Path(ctx["run_dir"]) / "user_filelist.tcl"
    shutil.copy2(filelist, dst)
    text = read.read_text(errors="replace")
    override = fixed["modules"][ctx["module"]]["filelist_override"]
    pattern = re.compile(r"\$MIPS_HOME/\.\./filelists/" + re.escape(override))
    if not pattern.search(text):
        raise BuildSourceError(f"{read} has no override branch for {override}; cannot install a custom filelist")
    new = pattern.sub(str(dst), text)
    import os, stat
    os.chmod(read, stat.S_IMODE(os.stat(read).st_mode) | stat.S_IWUSR)   # Perforce copies are r--
    read.write_text(new)
    log(f"custom filelist installed: {filelist} -> {dst}; read script now sources it")
    return dst
