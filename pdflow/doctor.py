"""`pdflow.sh doctor` (is this account/host set up to run the flow?) and `pdflow.sh report`
(one tar.gz with everything needed to look at a problem). Both read-only except the report file."""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import remote

ROOT = Path(__file__).resolve().parent.parent


class Doctor:
    def __init__(self):
        self.rows: list[tuple[str, str, str, str]] = []   # (result, check, detail, fix)

    def add(self, ok, check, detail="", fix=""):
        res = "PASS" if ok is True else ("WARN" if ok == "warn" else "FAIL")
        self.rows.append((res, check, detail, fix if res != "PASS" else ""))

    def print(self, out=print):
        w = max(len(r[1]) for r in self.rows)
        for res, check, detail, fix in self.rows:
            out(f"  {res:4}  {check:<{w}}  {detail}")
            if fix:
                out(f"        {'':<{w}}  -> {fix}")
        n_fail = sum(r[0] == "FAIL" for r in self.rows)
        n_warn = sum(r[0] == "WARN" for r in self.rows)
        out(f"\n{len(self.rows)} checks: {len(self.rows) - n_fail - n_warn} PASS, {n_warn} WARN, {n_fail} FAIL")
        return n_fail


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_doctor(fixed: dict) -> Doctor:
    d = Doctor()
    env = os.environ
    d.add(True, "host / user", f"{socket.gethostname()} / {env.get('USER', '?')}")

    # --- local Airflow side
    py = Path(env.get("AF_BASE_PY", "/usr/bin/python3.11"))
    d.add(py.is_file(), "python 3.11 on this host", str(py),
          "run Airflow on a host that has /usr/bin/python3.11 (mipscs82, mipscs83, mipscs85, mipspd81)")
    venv = Path(env.get("VENV", ROOT / "airflow" / "venv"))
    d.add((venv / "bin" / "airflow").is_file(), "Airflow installed (venv)", str(venv), "bash airflow/setup.sh")
    db = Path(env.get("AF_DB_DIR", "/var/tmp/x"))
    d.add((db / "airflow.db").is_file() or "warn", "metadata DB on this host", str(db),
          "created automatically by setup.sh / start.sh on this host")
    port = int(env.get("AIRFLOW_PORT", "0") or 0)
    base = env.get("AIRFLOW__API__BASE_URL", f"http://127.0.0.1:{port}")
    health = None
    try:
        import urllib.request
        with urllib.request.urlopen(f"{base}/api/v2/monitor/health", timeout=5) as r:
            health = json.loads(r.read().decode())
    except Exception:
        pass
    if health:
        bad = [k for k, v in health.items() if isinstance(v, dict) and v.get("status") != "healthy"]
        d.add(not bad or "warn", f"Airflow running on port {port}",
              "all healthy" if not bad else f"unhealthy: {', '.join(bad)}",
              "restart: bash airflow/stop.sh; tmux new -s pdflow 'bash airflow/start.sh'")
    elif _port_in_use(port):
        d.add(False, f"port {port}", "in use, but not by an Airflow that answers",
              "another program/user has it: set AIRFLOW_PORT in airflow/local.env")
    else:
        d.add("warn", f"Airflow running on port {port}", "not running",
              "tmux new -s pdflow 'bash airflow/start.sh'")

    # --- sources + run folder
    mh = Path(fixed["mips_home"])
    d.add(mh.is_dir(), "MIPS_HOME (Perforce workspace)", str(mh), "set mips_home in config/local.yaml")
    d.add(Path(fixed["rm_fc_cad_src"]).is_dir(), "rm_fc_cad source", fixed["rm_fc_cad_src"], "sync rm_fc_cad in that workspace")
    d.add(os.access(fixed["build_py"], os.X_OK), "build.py", fixed["build_py"], "sync sys_base in that workspace")
    rr = Path(fixed["runs_root"])
    probe = rr if rr.exists() else rr.parent
    writable = os.access(probe, os.W_OK)
    free = shutil.disk_usage(probe).free / 2**30 if probe.exists() else 0
    d.add(writable and (free >= 50 or "warn"), "runs_root writable + space", f"{rr}  ({free:.0f} GB free)",
          "set runs_root in config/local.yaml to a folder you own with >= 50 GB (corvi needs ~150 GB)")

    # --- pd hosts: ssh + FC environment
    hosts = fixed["fc_hosts"]
    with ThreadPoolExecutor(max_workers=len(hosts)) as ex:
        probes = dict(zip(hosts, ex.map(lambda h: remote.host_probe(fixed, h), hosts)))
    good = []
    for h, p in probes.items():
        if "error" in p:
            msg = p["error"]
            fix = ("first ssh once by hand to accept the host key: ssh " + h) if "Host key" in msg else \
                  "set up passwordless ssh: ssh-keygen -t ed25519; cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys"
            d.add(False, f"ssh {h}", msg[-120:], fix)
        else:
            good.append(h)
            d.add(True, f"ssh {h}", f"{p.get('nproc')} cores, load {p.get('load1')}, {p.get('mem_avail_gb')} GB free")
    if good:
        h = min(good, key=lambda x: probes[x].get("load1", 999) / max(1, probes[x].get("nproc", 1)))
        fe = remote.fc_env_probe(fixed, h)
        ok = fe.get("fc_shell", "").endswith("/fc_shell") and fe.get("license") == "set"
        d.add(ok, f"FC env on {h}", f"{fe.get('fc_shell') or fe.get('error', '?')} (license env {fe.get('license')})",
              f"'module load {fixed['fc_module']}' must work for your account on the pd hosts")
    return d


# --- report ----------------------------------------------------------------------------------
def _tail_bytes(p: Path, n_lines: int = 300) -> bytes:
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 256 * 1024))
            return b"\n".join(f.read().splitlines()[-n_lines:]) + b"\n"
    except OSError as e:
        return f"(unreadable: {e})\n".encode()


def make_report(fixed: dict, run_dir: Path, doctor_text: str) -> Path:
    m = json.loads((run_dir / "run.json").read_text())
    inp = m.get("inputs", {})
    mod_dir = Path(inp.get("module_dir", ""))
    out = run_dir / f"pdflow_report_{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    tag = run_dir.name

    def add_bytes(tf, name, data: bytes):
        ti = tarfile.TarInfo(f"{tag}/{name}")
        ti.size, ti.mtime = len(data), time.time()
        tf.addfile(ti, io.BytesIO(data))

    with tarfile.open(out, "w:gz") as tf:
        for name in ("summary.md", "run.json", "src_manifest.sha256"):
            if (run_dir / name).is_file():
                tf.add(run_dir / name, f"{tag}/{name}")
        for sub in ("patches", "status", "qor"):
            if (run_dir / sub).is_dir():
                tf.add(run_dir / sub, f"{tag}/{sub}")
        for f in sorted((run_dir / "logs").glob("*")):
            add_bytes(tf, f"logs/{f.name}", _tail_bytes(f, 500))
        for f in sorted((mod_dir / "rm_fc" / "logs").glob("*.log")) if mod_dir.is_dir() else []:
            add_bytes(tf, f"fc_logs/{f.name}.tail300", _tail_bytes(f))
        bl = Path(inp.get("rc_dir", "")) / "vbuild" / str(inp.get("build_config", "")) / "build.log"
        if bl.is_file():
            add_bytes(tf, "build.log.tail300", _tail_bytes(bl))
        # Airflow task logs of every Airflow run that touched this run dir
        logs = Path(os.environ.get("AIRFLOW_HOME", ROOT / "airflow" / "home")) / "logs" / "dag_id=raven_pd_flow"
        for h in m.get("history", []):
            rid = h.get("dag_run_id")
            d = logs / f"run_id={rid}"
            for f in sorted(d.glob("task_id=*/attempt=*.log")) if rid and d.is_dir() else []:
                add_bytes(tf, f"airflow_logs/{rid}/{f.parent.name}/{f.name}", _tail_bytes(f, 2000))
        try:
            git = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%H %cd %s"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            git = "(no git)"
        add_bytes(tf, "environment.txt", (f"code: {ROOT}\ngit: {git}\nhost: {socket.gethostname()}\n"
                                          f"user: {os.environ.get('USER')}\nfixed.yaml sha256: {fixed.get('_sha256')}\n"
                                          f"local config: {fixed.get('_local')}\n").encode())
        add_bytes(tf, "doctor.txt", doctor_text.encode())
    return out
