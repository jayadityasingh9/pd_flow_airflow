"""Command line for runs outside the UI.

    bash bin/pdflow.sh list [--live]          runs + live tool jobs
    bash bin/pdflow.sh status <run_tag>       per-step status from run.json
    bash bin/pdflow.sh qor <run_tag> [step]   parsed QoR of a step (default: latest)
    bash bin/pdflow.sh kill <run_tag> [step]  kill the live job of a run (TERM, then KILL)
    bash bin/pdflow.sh builds                 reusable RTL builds (paths for the form's "Build path")
    bash bin/pdflow.sh doctor                 is this account / host ready to run the flow?
    bash bin/pdflow.sh report <run_tag>       one .tar.gz with everything needed to look at a problem

  review gates from the terminal (same as the UI's "Required Actions"):
    bash bin/pdflow.sh gates                           gates waiting for a decision
    bash bin/pdflow.sh review  <run_tag>               show the QoR summary of its waiting gate
    bash bin/pdflow.sh approve <run_tag> [-m "comment"]
    bash bin/pdflow.sh reject  <run_tag> [-m "comment"]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
from pathlib import Path

from .config import QOR_STEPS, STEPS, load_fixed
from .qor import summarize_step, worst_setup
from .runner import kill_step, live_jobs
from .workspace import read_manifest


def _run_dir(fixed, tag) -> Path:
    d = Path(fixed["runs_root"]) / tag
    if not (d / "run.json").is_file():
        sys.exit(f"no run {tag!r} under {fixed['runs_root']}")
    return d


def cmd_list(fixed, a):
    live = live_jobs(fixed["runs_root"])
    print("live tool jobs:" if live else "live tool jobs: none")
    for j in live:
        print(f"  {j['run']:50} {j['step']:15} {j['host']}:{j['pid']}  since {j['since']}")
    if a.live:
        return
    root = Path(fixed["runs_root"])
    print(f"\nruns in {root}:")
    for d in sorted(root.glob("*/run.json"), key=lambda p: p.stat().st_mtime):
        m = json.loads(d.read_text())
        i = m.get("inputs", {})
        last = next((s for s in reversed(STEPS) if m.get("steps", {}).get(s, {}).get("status") not in (None, "pending")), "-")
        res = (m.get("history") or [{}])[-1].get("result", "running?")
        print(f"  {m.get('run_tag', d.parent.name):50} {i.get('module', '?'):13} last={last:15} "
              f"{m['steps'].get(last, {}).get('status', '-') if last != '-' else '-':8} {res}")


def cmd_status(fixed, a):
    d = _run_dir(fixed, a.tag)
    m = read_manifest(d)
    i = m["inputs"]
    print(f"{a.tag}: {i['module']} {i['build_config']} clk {i['clk_str']} ns, cores {i['max_cores']}, host {i['fc_host']}")
    for s in STEPS:
        st = m.get("steps", {}).get(s, {})
        dur = st.get("duration_s")
        print(f"  {s:15} {st.get('status', '-'):8} {st.get('host', ''):9} "
              f"{f'{dur / 3600:.2f} h' if dur else '':8} {'; '.join(st.get('fatal', []))[:120]}")
    for g, v in m.get("gates", {}).items():
        print(f"  gate after {g}: {v.get('decision')} {v.get('user') or ''} {v.get('comment') or ''}")
    for h in m.get("history", []):
        print(f"  history: {h.get('time')} {h.get('start_from')}->{h.get('stop_at')} {h.get('result', '')}")


def cmd_qor(fixed, a):
    d = _run_dir(fixed, a.tag)
    i = read_manifest(d)["inputs"]
    steps = [a.step] if a.step else [s for s in QOR_STEPS if (Path(i["module_dir"]) / "rm_fc" / "rpts" / s).is_dir()]
    for s in steps:
        q = summarize_step(Path(i["module_dir"]) / "rm_fc" / "rpts" / s, s)
        ws = worst_setup(q)
        h = q.get("global_timing", {}).get("hold", {})
        print(f"{s:15} setup WNS {ws.get('wns')} TNS {ws.get('tns')} NVP {ws.get('num')} | hold WNS {h.get('wns')} "
              f"| util {q.get('utilization')} | area {q.get('qor', {}).get('cell_area')} | missing {q['missing']}")


def cmd_kill(fixed, a):
    d = _run_dir(fixed, a.tag)
    jobs = [j for j in live_jobs(fixed["runs_root"]) if j["run"] == a.tag and (not a.step or j["step"] == a.step)]
    if not jobs:
        print("no live job for that run")
    for j in jobs:
        gone = kill_step(fixed, d, j["step"], log=print)
        print(f"{j['step']}: {'stopped' if gone else 'STILL ALIVE'}")


# --- gates through the Airflow REST API (the UI does the same calls) -------------------------
DAG_ID = "raven_pd_flow"
GATE_TASKS = {"gate_compile": "compile", "gate_clock_opt": "clock_opt_opto"}


def _api():
    import os
    import urllib.request
    # bin/pdflow.sh sources airflow/env.sh, so these are normally set; the fallbacks mirror env.sh.
    port = os.environ.get("AIRFLOW_PORT") or str(20000 + os.getuid() % 20000)
    base = os.environ.get("AIRFLOW__API__BASE_URL", f"http://127.0.0.1:{port}").rstrip("/")
    home = os.environ.get("AIRFLOW_HOME") or os.environ.get("PDFLOW_INSTANCE") \
        or str(Path(__file__).resolve().parent.parent / "airflow" / "home")
    pw_file = Path(home) / "simple_auth_manager_passwords.json.generated"
    try:
        pw = json.loads(pw_file.read_text())["admin"]
    except (OSError, KeyError, ValueError):
        sys.exit(f"cannot read the admin password from {pw_file}")

    def call(method, path, body=None, token=None):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            sys.exit(f"Airflow API {method} {path}: HTTP {e.code} {e.read().decode()[:300]}")
        except OSError as e:
            sys.exit(f"Airflow API not reachable at {base} ({e}); is Airflow running? (airflow/status.sh)")
    tok = call("POST", "/auth/token", {"username": "admin", "password": pw})["access_token"]
    return lambda method, path, body=None: call(method, path, body, tok)


def _pending(api) -> list[dict]:
    d = api("GET", f"/api/v2/dags/{DAG_ID}/dagRuns/~/hitlDetails?limit=100")
    out = []
    for h in d.get("hitl_details", []):
        ti = h.get("task_instance", {})
        if h.get("response_received") or ti.get("state") not in ("awaiting_input", "deferred"):
            continue
        out.append({"task_id": ti.get("task_id"), "run_id": ti.get("dag_run_id") or ti.get("run_id"),
                    "subject": h.get("subject", ""), "body": h.get("body", ""), "created": h.get("created_at", "")})
    return out


def _find_gate(fixed, api, tag: str) -> dict:
    d = _run_dir(fixed, tag)
    runs = {h.get("dag_run_id") for h in read_manifest(d).get("history", [])}
    mine = [g for g in _pending(api) if g["run_id"] in runs]
    if not mine:
        sys.exit(f"run {tag} has no gate waiting for a decision")
    return mine[-1]


def cmd_gates(fixed, a):
    gates = _pending(_api())
    if not gates:
        print("no gate is waiting for a decision")
    for g in gates:
        print(f"{g['task_id']:15} waiting since {g['created'][:19]}  {g['subject']}")


def cmd_review(fixed, a):
    g = _find_gate(fixed, _api(), a.tag)
    print(f"{g['subject']}\n({g['task_id']}, Airflow run {g['run_id']})\n")
    print(g["body"])


def cmd_decide(fixed, a):
    api = _api()
    g = _find_gate(fixed, api, a.tag)
    choice = "Approve" if a.cmd == "approve" else "Reject"
    print(f"{g['subject']}\n-> {choice}" + (f"  ({a.m})" if a.m else ""))
    api("PATCH", f"/api/v2/dags/{DAG_ID}/dagRuns/{g['run_id']}/taskInstances/{g['task_id']}/-1/hitlDetails",
        {"chosen_options": [choice], "params_input": {"comment": a.m or ""}})
    print(f"sent. The run continues with the next step." if choice == "Approve" else
          f"sent. The run stops here (FAILED); resume later with run_tag={a.tag} and a later start_from.")


def cmd_builds(fixed, a):
    """Every finished build under runs_root: tag, module, config, -tech, date, path to give the form."""
    from .buildsrc import build_tech_of
    root = Path(fixed["runs_root"])
    rows = []
    for pw in sorted(root.glob("*/rm_fc_cad/vbuild/*/vbuild/py.Work"), key=lambda p: p.stat().st_mtime, reverse=True):
        run = pw.parents[4]
        if not (pw / "synthesis_rtl.f").is_file() or (pw / "synthesis_rtl.f").stat().st_size == 0:
            continue
        try:
            inp = json.loads((run / "run.json").read_text())["inputs"]
        except (OSError, KeyError, ValueError):
            inp = {}
        rows.append((run.name, inp.get("module", "?"), pw.parents[1].name, build_tech_of(pw) or "?",
                     time.strftime("%Y-%m-%d", time.localtime(pw.stat().st_mtime)),
                     "reused" if (pw / "PDFLOW_REUSED_FROM").exists() else "built"))
    if not rows:
        print(f"no builds under {root}")
        return
    print(f"{'run (use as Build path, prefix ' + str(root) + '/)':62} {'module':13} {'config':24} {'-tech':40} {'date':10} how")
    for r in rows:
        print(f"{r[0]:62} {r[1]:13} {r[2]:24} {r[3]:40} {r[4]:10} {r[5]}")


def cmd_doctor(fixed, a):
    from .doctor import run_doctor
    print(f"pdflow doctor -- code {Path(__file__).resolve().parent.parent}")
    d = run_doctor(fixed)
    sys.exit(1 if d.print() else 0)


def cmd_report(fixed, a):
    import io
    import contextlib
    from .doctor import make_report, run_doctor
    d = _run_dir(fixed, a.tag)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_doctor(fixed).print()
    out = make_report(fixed, d, buf.getvalue())
    print(f"report written: {out}  ({out.stat().st_size / 1024:.0f} KB)")
    print("send this file (or its path -- it is on shared NFS) with: which step, which host, what you expected.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pdflow", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--live", action="store_true")
    p = sub.add_parser("status")
    p.add_argument("tag")
    p = sub.add_parser("qor")
    p.add_argument("tag")
    p.add_argument("step", nargs="?")
    p = sub.add_parser("kill")
    p.add_argument("tag")
    p.add_argument("step", nargs="?")
    sub.add_parser("gates")
    p = sub.add_parser("review")
    p.add_argument("tag")
    for c in ("approve", "reject"):
        p = sub.add_parser(c)
        p.add_argument("tag")
        p.add_argument("-m", default="", help="comment (saved in run.json / summary.md)")
    sub.add_parser("builds")
    sub.add_parser("doctor")
    p = sub.add_parser("report")
    p.add_argument("tag")
    a = ap.parse_args(argv)
    fixed = load_fixed()
    {"list": cmd_list, "status": cmd_status, "qor": cmd_qor, "kill": cmd_kill, "gates": cmd_gates,
     "review": cmd_review, "approve": cmd_decide, "reject": cmd_decide, "doctor": cmd_doctor, "builds": cmd_builds,
     "report": cmd_report}[a.cmd](fixed, a)


if __name__ == "__main__":
    main()
