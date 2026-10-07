"""Patch the RUN COPY's design_setup.tcl / fc_setup.tcl (clock period, max cores).

Every spec must match exactly once -- 0 or 2+ matches mean the file is not what we think it
is, and we stop rather than guess. Writes are atomic and read back; a unified diff of every
change is appended to <run>/patches/<module>.diff.
"""
from __future__ import annotations

import difflib
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path


class PatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class PatchSpec:
    id: str
    file: str          # relative to the module dir
    pattern: str       # MULTILINE regex
    replace: str       # re.sub template with {clk} / {cores} placeholders

    def rendered(self, values: dict) -> str:
        out = self.replace
        for k, v in values.items():
            out = out.replace("{" + k + "}", str(v))
        return out


def specs_from_fixed(fixed: dict) -> list[PatchSpec]:
    return [PatchSpec(s["id"], s["file"].replace("{tech_dir}", fixed["tech_dir"]), s["pattern"], s["replace"])
            for s in fixed["patches"]]


def count_matches(text: str, spec: PatchSpec) -> int:
    return len(re.findall(spec.pattern, text, flags=re.M))


def check_specs(module_dir, specs: list[PatchSpec]) -> list[str]:
    """Dry-run: problems ([] = every spec matches exactly once). Read-only."""
    probs = []
    for s in specs:
        f = Path(module_dir) / s.file
        if not f.is_file():
            probs.append(f"{s.id}: {f} not found")
            continue
        n = count_matches(f.read_text(errors="replace"), s)
        if n != 1:
            probs.append(f"{s.id}: pattern {s.pattern!r} matches {n}x in {f} (expected exactly 1)")
    return probs


def apply_patches(module_dir, specs: list[PatchSpec], values: dict, diff_path) -> list[dict]:
    """Apply all specs; returns [{id, file, before, after}]. Raises PatchError (nothing half-done
    per file: each file is rewritten once, atomically, after all its specs matched)."""
    module_dir = Path(module_dir)
    by_file: dict[str, list[PatchSpec]] = {}
    for s in specs:
        by_file.setdefault(s.file, []).append(s)

    changes = []
    for rel, fspecs in by_file.items():
        f = module_dir / rel
        if not f.is_file():
            raise PatchError(f"{f} not found")
        old = f.read_text(errors="replace")
        new = old
        for s in fspecs:
            m = list(re.finditer(s.pattern, new, flags=re.M))
            if len(m) != 1:
                raise PatchError(f"{s.id}: pattern matches {len(m)}x in {f} (expected exactly 1)")
            repl = s.rendered(values)
            before = m[0].group(0)
            after = m[0].expand(repl)
            new = new[: m[0].start()] + after + new[m[0].end():]
            changes.append({"id": s.id, "file": str(f), "before": before, "after": after})

        if new != old:
            mode = stat.S_IMODE(os.stat(f).st_mode) | stat.S_IWUSR
            tmp = f.with_name(f".{f.name}.pdflow_tmp")
            tmp.write_text(new)
            os.chmod(tmp, mode)
            os.replace(tmp, f)
            with open(diff_path, "a") as d:
                d.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                d.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                  fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        # read back: each spec now matches exactly once AND the match is the rendered text
        back = f.read_text(errors="replace")
        for s in fspecs:
            m = list(re.finditer(s.pattern, back, flags=re.M))
            want = [c["after"] for c in changes if c["id"] == s.id][-1]
            if len(m) != 1 or m[0].group(0) != want:
                raise PatchError(f"{s.id}: read-back of {f} does not show {want!r}")
    return changes


# --- on/off toggles (config `toggles:`) -----------------------------------------------------
@dataclass(frozen=True)
class Toggle:
    id: str
    param: str         # trigger-form boolean
    file: str          # relative to the module dir
    off_pattern: str   # matches the ORIGINAL text
    on_replace: str
    on_pattern: str    # matches the patched text
    off_replace: str


def toggles_from_fixed(fixed: dict) -> list[Toggle]:
    return [Toggle(t["id"], t["param"], t["file"], t["off_pattern"], t["on_replace"], t["on_pattern"],
                   t["off_replace"]) for t in fixed.get("toggles", [])]


def toggle_state(module_dir, t: Toggle) -> str:
    """'on' | 'off' | 'unknown' (neither form found exactly once)."""
    f = Path(module_dir) / t.file
    if not f.is_file():
        return "unknown"
    text = f.read_text(errors="replace")
    n_off = len(re.findall(t.off_pattern, text, flags=re.M))
    n_on = len(re.findall(t.on_pattern, text, flags=re.M))
    if n_off == 1 and n_on == 0:
        return "off"
    if n_on == 1 and n_off == 0:
        return "on"
    return "unknown"


def check_toggles(module_dir, toggles: list[Toggle]) -> list[str]:
    return [f"{t.id}: {Path(module_dir) / t.file} has neither the original nor the patched block exactly once"
            for t in toggles if toggle_state(module_dir, t) == "unknown"]


def apply_toggle(module_dir, t: Toggle, on: bool, diff_path) -> dict:
    """Switch the block on/off in the run copy (idempotent, reversible). Returns a change record."""
    f = Path(module_dir) / t.file
    state = toggle_state(module_dir, t)
    want = "on" if on else "off"
    if state == "unknown":
        raise PatchError(f"{t.id}: cannot find the expected block in {f}")
    if state == want:
        return {"id": t.id, "file": str(f), "before": state, "after": want}
    old = f.read_text(errors="replace")
    pat, rep = (t.off_pattern, t.on_replace) if on else (t.on_pattern, t.off_replace)
    new, n = re.subn(pat, rep, old, count=1, flags=re.M)
    if n != 1:
        raise PatchError(f"{t.id}: replace failed in {f}")
    mode = stat.S_IMODE(os.stat(f).st_mode) | stat.S_IWUSR
    tmp = f.with_name(f".{f.name}.pdflow_tmp")
    tmp.write_text(new)
    os.chmod(tmp, mode)
    os.replace(tmp, f)
    with open(diff_path, "a") as d:
        d.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')}  toggle {t.id} -> {want}\n")
        d.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                          fromfile=f"a/{t.file}", tofile=f"b/{t.file}"))
    if toggle_state(module_dir, t) != want:
        raise PatchError(f"{t.id}: read-back of {f} is not '{want}'")
    return {"id": t.id, "file": str(f), "before": state, "after": want}


# --- RTL read fixes on the run's build output (config `rtl_fixes:`) --------------------------
def rtl_include_root(ctx: dict) -> Path:
    return Path(ctx["rc_dir"]) / "vbuild" / ctx["build_config"] / "vbuild" / "include"


def apply_rtl_fixes(ctx: dict, fixed: dict, diff_path) -> list[dict]:
    """Patch the RUN's copy of generated/included RTL headers (never Perforce). Per fix:
    1 match -> applied; 0 matches + done_pattern present -> 'already'; 0 and no done form ->
    'not-needed' (fixed upstream or file differs); 2+ -> PatchError."""
    out = []
    for fx in fixed.get("rtl_fixes", []) or []:
        f = rtl_include_root(ctx) / fx["file"]
        rec = {"id": fx["id"], "file": str(f)}
        if not f.is_file():
            out.append({**rec, "status": "file-missing"})
            continue
        old = f.read_text(errors="replace")
        n = len(re.findall(fx["pattern"], old, flags=re.M))
        if n > 1:
            raise PatchError(f"rtl fix {fx['id']}: pattern matches {n}x in {f}")
        if n == 0:
            done = fx.get("done_pattern") and re.search(fx["done_pattern"], old, flags=re.M)
            out.append({**rec, "status": "already" if done else "not-needed"})
            continue
        new = re.sub(fx["pattern"], fx["replace"], old, count=1, flags=re.M)
        mode = stat.S_IMODE(os.stat(f).st_mode) | stat.S_IWUSR
        tmp = f.with_name(f".{f.name}.pdflow_tmp")
        tmp.write_text(new)
        os.chmod(tmp, mode)
        os.replace(tmp, f)
        with open(diff_path, "a") as d:
            d.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')}  rtl fix {fx['id']} (run copy of build output)\n")
            d.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                              fromfile=f"a/{fx['file']}", tofile=f"b/{fx['file']}"))
        if re.findall(fx["pattern"], f.read_text(errors="replace"), flags=re.M):
            raise PatchError(f"rtl fix {fx['id']}: read-back of {f} still shows the original")
        out.append({**rec, "status": "applied"})
    return out
