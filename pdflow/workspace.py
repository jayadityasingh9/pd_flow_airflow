"""Per-run directory: fresh copy of rm_fc_cad, run.json manifest, lock."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import stat
import time
from pathlib import Path

from .config import FC_STEPS, STEPS, step_index

LOCK = ".pdflow.lock"
OK_STATES = ("ok", "warn")


class WorkspaceError(RuntimeError):
    pass


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# --- manifest (run.json) --------------------------------------------------------------------
def manifest_path(run_dir) -> Path:
    return Path(run_dir) / "run.json"


def read_manifest(run_dir) -> dict:
    p = manifest_path(run_dir)
    return json.loads(p.read_text()) if p.is_file() else {}


def write_manifest(run_dir, data: dict) -> None:
    p = manifest_path(run_dir)
    tmp = p.with_suffix(f".json.tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False, default=str) + "\n")
    os.replace(tmp, p)


def update_manifest(run_dir, fn) -> dict:
    """Read-modify-write. Tasks of one run are sequential and the run lock excludes other DAG
    runs, so there is a single writer at a time (no NFS file locking needed)."""
    data = read_manifest(run_dir)
    fn(data)
    write_manifest(run_dir, data)
    return data


def set_step(run_dir, step: str, **fields) -> dict:
    def _f(m):
        m.setdefault("steps", {}).setdefault(step, {}).update(fields)
    return update_manifest(run_dir, _f)


def step_state(run_dir, step: str) -> dict:
    return read_manifest(run_dir).get("steps", {}).get(step, {})


# --- lock ---------------------------------------------------------------------------------
def acquire_lock(run_dir, dag_run_id: str, owner_finished=None, log=print) -> None:
    """owner_finished(dag_run_id) -> True if that Airflow run is over (e.g. 'Mark failed' on the
    whole run skips finalize, which is what normally releases the lock): its lock is then taken over."""
    lp = Path(run_dir) / LOCK
    mine = {"dag_run_id": dag_run_id, "host": socket.gethostname(), "time": now_iso()}
    if lp.exists():
        cur = json.loads(lp.read_text() or "{}")
        if cur.get("dag_run_id") != dag_run_id and owner_finished and owner_finished(cur.get("dag_run_id")):
            log(f"lock of finished Airflow run {cur.get('dag_run_id')} taken over")
            tmp = lp.with_name(lp.name + f".{os.getpid()}")
            tmp.write_text(json.dumps(mine))
            os.replace(tmp, lp)
            return
        if cur.get("dag_run_id") != dag_run_id:
            raise WorkspaceError(
                f"run {Path(run_dir).name} is locked by Airflow run {cur.get('dag_run_id')} "
                f"(host {cur.get('host')}, since {cur.get('time')}). If that run is finished or dead, "
                f"delete {lp} and trigger again.")
        return
    fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    with os.fdopen(fd, "w") as f:
        json.dump(mine, f)


def release_lock(run_dir, dag_run_id: str) -> None:
    lp = Path(run_dir) / LOCK
    try:
        cur = json.loads(lp.read_text() or "{}")
    except FileNotFoundError:
        return
    if cur.get("dag_run_id") == dag_run_id:
        lp.unlink(missing_ok=True)


# --- copy -----------------------------------------------------------------------------------
def _make_writable(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            m = os.stat(p).st_mode
            os.chmod(p, m | stat.S_IWUSR)
    os.chmod(root, os.stat(root).st_mode | stat.S_IWUSR)


def write_src_manifest(src: Path, out: Path) -> int:
    """sha256 of every source file (proves later that the Perforce copy was never modified)."""
    n = 0
    with open(out, "w") as f:
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames.sort()
            for name in sorted(filenames):
                p = Path(dirpath) / name
                if p.is_symlink() or not p.is_file():
                    continue
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                f.write(f"{h}  {p.relative_to(src)}\n")
                n += 1
    return n


def create_workspace(ctx: dict, fixed: dict, log=print) -> None:
    """New run: <runs_root>/<tag>/ + a fresh, writable copy of rm_fc_cad. Fails if tag exists."""
    run_dir = Path(ctx["run_dir"])
    src = Path(fixed["rm_fc_cad_src"])
    Path(fixed["runs_root"]).mkdir(parents=True, exist_ok=True)
    try:
        run_dir.mkdir()
    except FileExistsError:
        raise WorkspaceError(f"{run_dir} already exists: pick another run_tag, or resume it with "
                             f"start_from > build") from None
    try:
        for sub in ("scripts", "status", "logs", "qor", "patches"):
            (run_dir / sub).mkdir()
        acquire_lock(run_dir, ctx["dag_run_id"])
        log(f"copying {src} -> {ctx['rc_dir']}")
        shutil.copytree(src, ctx["rc_dir"], symlinks=True)
        _make_writable(Path(ctx["rc_dir"]))  # Perforce files are r--; the run copy must be patchable
        n = write_src_manifest(src, run_dir / "src_manifest.sha256")
    except BaseException:
        # we created run_dir in this call: remove the half-made copy so the tag is not blocked
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    log(f"copied; {n} source files hashed into src_manifest.sha256")


def open_existing(ctx: dict, owner_finished=None) -> dict:
    """Resume: the run dir must exist with run.json; take the lock."""
    run_dir = Path(ctx["run_dir"])
    if not manifest_path(run_dir).is_file():
        raise WorkspaceError(f"resume: {manifest_path(run_dir)} not found")
    acquire_lock(run_dir, ctx["dag_run_id"], owner_finished)
    for sub in ("scripts", "status", "logs", "qor", "patches"):
        (run_dir / sub).mkdir(exist_ok=True)
    return read_manifest(run_dir)


# --- reuse verification --------------------------------------------------------------------
def synthesis_files(ctx: dict) -> list[Path]:
    w = Path(ctx["rc_dir"]) / "vbuild" / ctx["build_config"] / "vbuild" / "py.Work"
    return [w / f"synthesis_{k}.f" for k in ("rtl", "inc", "pkg")]


def fc_tmp(ctx: dict) -> Path:
    return Path(ctx["module_dir"]) / "rm_fc" / "tmp"


def verify_reusable(ctx: dict, step: str) -> list[str]:
    """Problems that make `step` unusable as the base of a resumed run ([] = fine)."""
    probs = []
    st = step_state(ctx["run_dir"], step)
    if st.get("status") not in OK_STATES:
        probs.append(f"{step}: status in run.json is {st.get('status') or 'missing'} (needs ok/warn)")
    if step == "build":
        if ctx.get("build_source") == "filelist":
            if not (Path(ctx["run_dir"]) / "user_filelist.tcl").is_file():
                probs.append("build: the run's user_filelist.tcl is missing")
            return [p for p in probs if "status in run.json" not in p] + \
                ([] if st.get("status") in OK_STATES + ("provided",) else [f"build: status {st.get('status')}"])
        for f in synthesis_files(ctx):
            if not f.is_file() or f.stat().st_size == 0:
                probs.append(f"build: {f} missing/empty")
        if st.get("status") == "provided":
            probs = [p for p in probs if "status in run.json" not in p]
    else:
        tmp = fc_tmp(ctx)
        marker = tmp / step
        if not marker.is_file():
            probs.append(f"{step}: marker {marker} missing")
        if not (tmp / f"{ctx['design_name']}.nlib").is_dir():
            probs.append(f"{step}: design library {tmp / (ctx['design_name'] + '.nlib')} missing")
        i = FC_STEPS.index(step)
        if i > 0 and marker.is_file():
            prev = tmp / FC_STEPS[i - 1]
            if prev.is_file() and prev.stat().st_mtime > marker.stat().st_mtime + 1:
                probs.append(f"{step}: marker older than {FC_STEPS[i - 1]}'s (earlier step was re-run later)")
    return probs


def steps_between(start: str, stop: str) -> list[str]:
    return STEPS[step_index(start): step_index(stop) + 1]
