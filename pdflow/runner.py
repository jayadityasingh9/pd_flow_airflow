"""Launch a step on a pd host (detached) and follow it to completion.

The job writes <run>/status/<step>.{pid,start,rc,end} on shared NFS (atomically: temp + mv);
this side only reads those files, tails the tool log into the Airflow task log, and checks
liveness over ssh now and then. Because the job is detached (its own session via setsid over
ssh), it survives an Airflow restart: the next attempt RE-ATTACHES to it (still running) or
ADOPTS its result (finished while nobody was watching) instead of launching a duplicate.

Launch bookkeeping: every launch gets a launch_id (status/<step>.launch). Once an attempt has
evaluated a launch's result it records that launch_id in status/<step>.consumed; a consumed
result is never adopted again, so the next Clear/resume launches fresh.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import remote
from .scripts import launcher_path, script_path, tool_log

# Lines worth copying live into the Airflow task log (the full logs stay on disk).
STREAM_RE = re.compile(
    r"^(Error|Fatal|RM-error)\b|FLW-800[01]|FLW-8100|Thank you for using|"
    r" - CRITICAL - | - ERROR - |Error-\[|^pdflow:|Traceback|No clocks created|Rule S19|"
    r"^Information: Timer using \d+ threads|^\s*set_host_options -max_cores|"
    r"(?i:license).*(queue|wait|denied|checkout)")
# First sign that fc_shell got its license and is executing the RM scripts (fc_setup.tcl is
# the first thing every step sources, with -echo). Time before this is license queueing.
FC_ACTIVE_RE = re.compile(r"^\s*(source -echo|set_host_options|RM-info)")


@dataclass
class RunResult:
    rc: int | None
    outcome: str            # done | timeout | lost
    host: str
    pid: int | None
    start_epoch: float | None
    end_epoch: float | None
    reattached: str         # "" | "running" | "finished"
    elapsed_s: float
    launch_id: str
    queued_s: float = 0.0   # time before the tool became active (license queue)

    def as_dict(self) -> dict:
        return asdict(self)


def status_files(run_dir, step) -> dict:
    st = Path(run_dir) / "status"
    return {k: st / f"{step}.{k}" for k in ("pid", "rc", "start", "end", "launch", "consumed")}


def _read(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except (FileNotFoundError, OSError):
        return None


def _read_num(p: Path, typ=int):
    s = _read(p)
    try:
        return typ(s.split()[0]) if s else None
    except (ValueError, IndexError):
        return None


def read_pid(run_dir, step) -> tuple[str, int] | None:
    s = _read(status_files(run_dir, step)["pid"])
    try:
        host, pid = s.split()[:2]
        return host, int(pid)
    except (AttributeError, ValueError):
        return None


def read_launch(run_dir, step) -> dict:
    try:
        return json.loads(_read(status_files(run_dir, step)["launch"]) or "{}")
    except json.JSONDecodeError:
        return {}


def job_ref(run_dir, step) -> tuple[str, int] | None:
    """(host, session pid) from the pid file, else from what the launcher printed."""
    info = read_pid(run_dir, step)
    if info:
        return info
    li = read_launch(run_dir, step)
    if li.get("host") and li.get("leader_pid"):
        return li["host"], int(li["leader_pid"])
    return None


def mark_consumed(run_dir, step, launch_id: str) -> None:
    status_files(run_dir, step)["consumed"].write_text(launch_id + "\n")


def consume_finished(run_dir, step) -> bool:
    """Resume helper: if the step has a finished-but-unconsumed result, mark it consumed so the
    resumed run launches fresh instead of adopting an old result. True if something changed."""
    sf = status_files(run_dir, step)
    lid = read_launch(run_dir, step).get("launch_id")
    if sf["rc"].exists() and lid and _read(sf["consumed"]) != lid:
        mark_consumed(run_dir, step, lid)
        return True
    return False


class Tailer:
    """Incrementally read complete new lines of a log that may not exist yet / be replaced."""

    def __init__(self, path: Path, not_before: float | None = None):
        self.path, self.not_before = Path(path), not_before
        self.ino, self.pos, self.buf = None, 0, ""

    def poll(self, max_bytes: int = 8 << 20) -> list[str]:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return []
        if self.not_before and st.st_mtime < self.not_before - 2:
            return []                       # still the previous attempt's log (not rotated yet)
        if st.st_ino != self.ino or st.st_size < self.pos:
            self.ino, self.pos, self.buf = st.st_ino, 0, ""
        if st.st_size == self.pos:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            chunk = f.read(max_bytes)
        self.pos += len(chunk)
        text = self.buf + chunk.decode("utf-8", "replace")
        lines = text.split("\n")
        self.buf = lines.pop()
        return lines

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0


def kill_step(fixed: dict, run_dir, step: str, grace_s: int = 60, log=print) -> bool:
    """SIGTERM the job's whole session, SIGKILL after grace_s (done on the pd host itself).
    True only if the session is verifiably empty afterwards."""
    ref = job_ref(run_dir, step)
    if not ref:
        log(f"kill {step}: no pid known")
        return True
    host, pid = ref
    a = remote.alive(fixed, host, pid, str(run_dir))
    if a is False:
        return True
    if a is None:
        log(f"kill {step}: cannot reach {host} to check pid {pid}")
        return False
    log(f"kill {step}: SIGTERM session {pid} on {host} (SIGKILL after {grace_s}s)")
    lid = read_launch(run_dir, step).get("launch_id")
    if lid:
        mark_consumed(run_dir, step, lid)       # a deliberate kill: the next attempt launches fresh
    try:
        out = remote.run_sh(fixed, host, remote.kill_script(pid, grace_s, str(Path(run_dir) / "status" / step)),
                            timeout=grace_s + 60)
    except remote.RemoteError as e:
        log(f"kill {step}: {e}")
        return False
    log(f"kill {step}: {out.strip()}")
    return "KILLED" in out


def run_or_reattach(fixed: dict, ctx: dict, step: str, host: str, soft_timeout_s: float | None, attempt: str,
                    log=print, poll_s: float = 10, alive_every_s: float = 120, heartbeat_s: float = 300,
                    start_wait_s: float = 180, license_wait_s: float | None = None,
                    stall_timeout_s: float | None = None) -> RunResult:
    """soft_timeout_s = max ACTIVE runtime (None = no cap); stall_timeout_s = stop only if the tool
    log has not grown for this long (None = off). License queueing counts towards neither."""
    run_dir = ctx["run_dir"]
    sf = status_files(run_dir, step)
    script = script_path(ctx, step)
    reattached = ""
    if license_wait_s is None:
        license_wait_s = float(fixed.get("license_env", {}).get("SNPS_MAX_WAITTIME", 0) or 0)

    info = read_pid(run_dir, step)
    li = read_launch(run_dir, step)
    launch_id = li.get("launch_id", "")
    consumed = _read(sf["consumed"])
    if info and not sf["rc"].exists():
        h, pid = info
        a = remote.alive(fixed, h, pid, run_dir)
        if a is True:
            reattached, host = "running", h
            log(f"REATTACHED: {step} is still running on {h} (session {pid}) -- not launching a duplicate")
        elif a is None:
            raise remote.RemoteError(f"cannot reach {h} to check the earlier {step} job (session {pid}); "
                                     f"refusing to launch a possible duplicate. Retry when {h} answers.")
        else:
            log(f"earlier {step} job (session {pid} on {h}) is gone without an exit code (killed/crashed): relaunching")
    elif info and sf["rc"].exists() and launch_id and launch_id != consumed:
        reattached, host = "finished", info[0]
        log(f"ADOPTED: {step} finished on {info[0]} while no Airflow task was watching "
            f"(launch {launch_id}); evaluating that result instead of re-running")

    if not reattached:
        for k in ("pid", "rc", "start", "end"):
            sf[k].unlink(missing_ok=True)
        launch_id = f"{attempt}@{time.strftime('%Y%m%dT%H%M%S')}"
        rec = {"launch_id": launch_id, "attempt": attempt, "host": host, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
        sf["launch"].write_text(json.dumps(rec) + "\n")
        log(f"launching {script.name} on {host}")
        out, leader = remote.launch(fixed, host, str(launcher_path(ctx, step)))
        log(out)
        if leader:
            rec["leader_pid"] = leader
            sf["launch"].write_text(json.dumps(rec) + "\n")
        t0 = time.time()
        while not read_pid(run_dir, step):             # parsed, not merely created
            if time.time() - t0 > start_wait_s:
                wl = Path(run_dir) / "logs" / f"{step}.wrapper.log"
                tail = (_read(wl) or "")[-2000:]
                raise remote.RemoteError(f"{step} did not start on {host} within {start_wait_s}s "
                                         f"(no {sf['pid']}). Wrapper log tail:\n{tail}")
            time.sleep(3)

    h, pid = read_pid(run_dir, step)
    t0 = time.time()
    while _read_num(sf["start"], float) is None and not sf["rc"].exists() and time.time() - t0 < 60:
        time.sleep(2)
    start_epoch = _read_num(sf["start"], float)
    t_start = start_epoch or time.time()
    is_fc = step != "build"
    active_since = None if is_fc else t_start
    tails = [Tailer(tool_log(ctx, step), start_epoch),
             Tailer(Path(run_dir) / "logs" / f"{step}.wrapper.log", start_epoch)]
    last_alive = last_beat = last_growth = time.time()
    last_size = -1
    outcome = "done"

    def pump():
        nonlocal active_since
        for t in tails:
            for line in t.poll():
                if active_since is None and t is tails[0] and FC_ACTIVE_RE.match(line):
                    active_since = time.time()
                    log(f"{step}: fc_shell is active (license obtained) after "
                        f"{(active_since - t_start) / 60:.1f} min")
                if STREAM_RE.search(line):
                    log(f"[{t.path.name}] {line[:400]}")

    while not sf["rc"].exists():
        pump()
        now = time.time()
        if now - last_alive >= alive_every_s:
            last_alive = now
            if remote.alive(fixed, h, pid, run_dir) is False:
                # NFS attribute caching can hide a fresh rc file for a while: give it a minute
                for _ in range(6):
                    time.sleep(10)
                    if sf["rc"].exists():
                        break
                else:
                    outcome = "lost"
                    log(f"{step}: job session {pid} on {h} is gone and wrote no exit code (killed, crashed, host reboot?)")
                    break
        size_now = tails[0].size()
        if size_now != last_size:
            last_size, last_growth = size_now, now
        # limits count ACTIVE time only; waiting in the license queue has its own budget
        what = None
        if active_since is None:
            if now - t_start > (soft_timeout_s or 0) + license_wait_s + (stall_timeout_s or 0):
                what = f"never became active within {(now - t_start) / 3600:.1f} h (license queue / start-up?)"
        elif stall_timeout_s and now - last_growth > stall_timeout_s:
            what = (f"no new output in {tails[0].path.name} for {(now - last_growth) / 3600:.1f} h "
                    f"(stall timeout {stall_timeout_s / 3600:.0f} h): the tool looks hung")
        elif soft_timeout_s and now - active_since > soft_timeout_s:
            what = f"max runtime {soft_timeout_s / 3600:.0f} h exceeded"
        if what:
            outcome = "timeout"
            log(f"{step}: {what} -> killing the job")
            kill_step(fixed, run_dir, step, log=log)
            break
        if now - last_beat >= heartbeat_s:
            last_beat = now
            size = tails[0].size()
            what = (f"{tails[0].path.name} {size / 1e6:.1f} MB" if size else
                    f"no output in {tails[0].path.name} yet (fc_shell start-up / license checkout can take minutes)")
            state = "" if active_since is not None or not is_fc else " [waiting for license/start-up]"
            log(f"... {step} running on {h} session {pid}: {(now - t_start) / 60:.0f} min elapsed{state}, {what}")
        time.sleep(poll_s)
    pump()

    rc = None
    if outcome == "done":
        for _ in range(15):                       # an rc file seen mid-rename/NFS lag: re-read briefly
            rc = _read_num(sf["rc"], int)
            if rc is not None:
                break
            time.sleep(2)
    end_epoch = _read_num(sf["end"], float)
    queued = ((active_since or t_start) - t_start) if is_fc else 0.0
    return RunResult(rc=rc, outcome=outcome, host=h, pid=pid, start_epoch=start_epoch, end_epoch=end_epoch,
                     reattached=reattached, elapsed_s=round((end_epoch or time.time()) - t_start, 1),
                     launch_id=launch_id, queued_s=round(queued, 1))


def live_jobs(runs_root) -> list[dict]:
    """Every status/*.pid without an rc file under runs_root (cheap: no ssh)."""
    out = []
    root = Path(runs_root)
    if not root.is_dir():
        return out
    for pidf in root.glob("*/status/*.pid"):
        step = pidf.stem
        if not (pidf.parent / f"{step}.rc").exists():
            s = (_read(pidf) or "").split()
            out.append({"run": pidf.parent.parent.name, "step": step,
                        "host": s[0] if s else "?", "pid": s[1] if len(s) > 1 else "?",
                        "since": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(pidf)))})
    return sorted(out, key=lambda d: d["since"])
