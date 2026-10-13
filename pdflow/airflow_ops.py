"""The two custom Airflow operators (the only module that imports Airflow).

PdStepOperator  one flow step (build or an FC step): reuse / skip / dry / run(+re-attach) + checks
PdQorGate       ApprovalOperator that shows the QoR table, or auto-approves when not needed
"""
from __future__ import annotations

import json
import os
import time
from datetime import timedelta
from pathlib import Path

from airflow.providers.standard.exceptions import HITLRejectException, HITLTimeoutError
from airflow.providers.standard.operators.hitl import ApprovalOperator
from airflow.sdk import BaseOperator, Param
from airflow.sdk.exceptions import AirflowException, AirflowFailException, AirflowSkipException, AirflowTaskTimeout

from . import remote
from .checks import check_build, check_fc_step
from .config import QOR_STEPS, load_fixed
from .context import gate_needed, step_mode
from .qor import render_markdown, summarize_step
from .patch import PatchError, apply_rtl_fixes
from .runner import job_ref, kill_step, mark_consumed, read_launch, run_or_reattach
from .scripts import script_path, tool_log
from .workspace import (WorkspaceError, acquire_lock, now_iso, read_manifest, set_step, step_state,
                        update_manifest, verify_reusable)

SHUTDOWN_FLAG = "pdflow_shutdown"          # written by airflow/stop.sh; see on_kill
# task states that mean a PERSON stopped the task ('Mark failed' / 'Mark success' in the UI)
USER_STOP_STATES = {"failed", "success", "skipped", "removed", "upstream_failed"}


def _db():
    conn = os.environ.get("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "")
    if not conn.startswith("sqlite:///"):
        return None
    import sqlite3
    return sqlite3.connect(f"file:{conn[len('sqlite:///'):]}?mode=ro", uri=True, timeout=3)


def dag_run_finished(run_id) -> bool:
    """True only if that Airflow run is recorded as finished (success/failed) in this Airflow's DB."""
    try:
        db = _db()
        if db is None or not run_id:
            return False
        try:
            row = db.execute("select state from dag_run where run_id = ?", (run_id,)).fetchone()
        finally:
            db.close()
        return bool(row) and row[0] in ("success", "failed")
    except Exception:
        return False


def _ti_state(ti_id) -> str | None:
    """This task instance's state, read straight from the (node-local SQLite) metadata DB.
    None if it cannot be read -- callers must then NOT kill anything."""
    conn = os.environ.get("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "")
    if not ti_id or not conn.startswith("sqlite:///"):
        return None
    import sqlite3
    try:
        db = sqlite3.connect(f"file:{conn[len('sqlite:///'):]}?mode=ro", uri=True, timeout=3)
        try:
            row = db.execute("select state from task_instance where id = ?",
                             (str(ti_id).replace("-", ""),)).fetchone()
        finally:
            db.close()
        return row[0] if row else None
    except Exception:
        return None


def _ctx(context) -> dict:
    ctx = context["ti"].xcom_pull(task_ids="resolve_inputs")
    if not ctx:
        raise AirflowFailException("no resolved inputs (resolve_inputs failed)")
    return ctx


def _relock(ctx: dict) -> None:
    """finalize releases the run lock; a Clear/retry of a step must take it again (no-op for the
    owner, fails fast if another Airflow run now owns this run dir)."""
    try:
        acquire_lock(ctx["run_dir"], ctx["dag_run_id"], dag_run_finished)
    except WorkspaceError as e:
        raise AirflowFailException(str(e)) from None


def _airflow_healthy(timeout: float = 1.5) -> bool:   # kept for status checks
    """Is the API server + metadata DB answering? (Distinguishes a deliberate 'Mark failed' from an
    Airflow outage, where the supervisor also SIGTERMs tasks after failed heartbeats.)"""
    import urllib.request
    base = os.environ.get("AIRFLOW__API__BASE_URL", "http://127.0.0.1:8081").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/v2/monitor/health", timeout=timeout) as r:
            d = json.loads(r.read().decode() or "{}")
        return r.status == 200 and d.get("metadatabase", {}).get("status") == "healthy"
    except Exception:
        return False


def _tail(path: Path, n: int = 40) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 64 * 1024))
            return "\n".join(f.read().decode("utf-8", "replace").splitlines()[-n:])
    except OSError:
        return "(log not readable)"


class PdStepOperator(BaseOperator):
    ui_color = "#dbe9f6"

    def __init__(self, *, step: str, **kwargs):
        super().__init__(**kwargs)
        self.step = step
        self._run = None                  # (fixed, ctx) once a job is launched/attached
        self._ti_id = None                # this attempt's task-instance id (for on_kill)

    def execute(self, context):
        ctx = _ctx(context)
        fixed = load_fixed()
        step, run_dir, log = self.step, ctx["run_dir"], self.log.info
        mode = step_mode(ctx, step)

        if mode == "skip":
            if step_state(run_dir, step).get("status") in (None, "pending"):
                set_step(run_dir, step, status="skipped", time=now_iso())
            raise AirflowSkipException(f"{step} comes after stop_at={ctx['stop_at']}")
        if mode == "reuse":
            probs = verify_reusable(ctx, step)
            if probs:
                raise AirflowFailException("cannot reuse:\n" + "\n".join(probs))
            log(f"REUSED {step} from run {ctx['run_tag']} (start_from={ctx['start_from']}); status "
                f"{step_state(run_dir, step).get('status')}")
            return {"mode": "reuse"}
        if mode == "provided":
            from .buildsrc import BuildSourceError, check_existing, check_filelist, install_existing, install_filelist
            _relock(ctx)
            try:
                if ctx["build_source"] == "existing":
                    r = check_existing(ctx["build_path"], ctx["build_tech"])
                    where = install_existing(ctx, r["pywork"], log=log) if not ctx["dry_run"] else "(dry run)"
                    note = f"reused build {r['pywork']} ({r['rtl_files']} RTL files, -tech {r['build_tech']})"
                else:
                    r = check_filelist(ctx["build_path"])
                    where = install_filelist(ctx, fixed, r["filelist"], log=log) if not ctx["dry_run"] else "(dry run)"
                    note = f"custom filelist {r['filelist']} ({r['rtl_files']} RTL files, {r['inc_dirs']} include dirs)"
            except BuildSourceError as e:
                set_step(run_dir, step, status="fail", fatal=[str(e)], ended=now_iso())
                raise AirflowFailException(f"build source: {e}") from None
            log(f"BUILD STEP NOT RUN: {note} -> {where}")
            set_step(run_dir, step, status="dry" if ctx["dry_run"] else "provided", source=ctx["build_source"],
                     path=ctx["build_path"], note=note, warnings=r.get("warnings", []), time=now_iso())
            return {"mode": "provided", "note": note}
        if mode == "dry":
            sp = script_path(ctx, step)
            log(f"DRY RUN: would launch {sp} on {ctx['fc_host']}:\n{sp.read_text()}")
            if step_state(run_dir, step).get("status") not in ("ok", "warn"):
                set_step(run_dir, step, status="dry", time=now_iso())
            return {"mode": "dry"}

        _relock(ctx)
        self._ti_id = getattr(context["ti"], "id", None)
        rtl_fix_note = []
        if step == "init_design":
            # FC reads the run's copy of the build's include files: apply the configured RTL read
            # fixes there (idempotent; covers new runs and resumes from init_design alike)
            try:
                fixes = apply_rtl_fixes(ctx, fixed, Path(run_dir) / "patches" / f"{ctx['module']}.diff")
            except PatchError as e:
                raise AirflowFailException(str(e)) from None
            for fx in fixes:
                log(f"rtl fix {fx['id']}: {fx['status']}  ({fx['file']})")
                if fx["status"] in ("applied", "already"):
                    rtl_fix_note.append(f"RTL read fix '{fx['id']}' {fx['status']} in the run's build output "
                                        f"(see config/fixed.yaml rtl_fixes; report upstream)")
        attempt = f"{context['run_id']}:{context['ti'].try_number}"
        host = ctx["fc_host"]
        cap_h = (fixed.get("max_runtime_h") or {}).get(ctx["size"])
        soft = float(cap_h) * 3600 if cap_h else None
        stall = float(fixed.get("stall_timeout_h") or 0) * 3600 or None
        set_step(run_dir, step, status="running", host=host, started=now_iso(), attempt=attempt,
                 fatal=[], warnings=[], facts={})
        (Path(run_dir) / "qor" / f"{step}.json").unlink(missing_ok=True)
        self._run = (fixed, ctx)
        try:
            res = run_or_reattach(fixed, ctx, step, host, soft, attempt, log=log, stall_timeout_s=stall).as_dict()
        except remote.RemoteError as e:
            # ssh / host trouble is TRANSIENT: plain AirflowException so Airflow RETRIES the task
            # (a retry re-attaches to a job that is still running, or relaunches a dead one).
            # Real flow failures below use AirflowFailException = fail at once, no retry.
            self._run = None
            set_step(run_dir, step, status="fail", fatal=[f"ssh/host problem (will retry): {e}"], ended=now_iso())
            raise AirflowException(f"{step}: ssh/host problem, Airflow will retry: {e}") from None
        except AirflowTaskTimeout:
            # Airflow's own (hard) execution_timeout: stop the remote job too. self._run stays set
            # so the SDK's on_kill() that follows also knows the job.
            set_step(run_dir, step, status="fail", fatal=["Airflow execution_timeout reached"], ended=now_iso())
            kill_step(fixed, run_dir, step, grace_s=30, log=log)
            raise
        self._run = None

        allow = set((fixed.get("allowed_error_ids") or {}).get(step, []) or [])
        v = check_build(ctx, res) if step == "build" else check_fc_step(ctx, fixed, step, res, allow_ids=allow)
        for n in rtl_fix_note:
            v.warn(n)
        if res.get("queued_s", 0) > 60:
            v.facts["license_queue_min"] = round(res["queued_s"] / 60, 1)
        if step in QOR_STEPS:
            rpt = Path(ctx["module_dir"]) / "rm_fc" / "rpts" / step
            if rpt.is_dir():
                q = summarize_step(rpt, step)
                (Path(run_dir) / "qor" / f"{step}.json").write_text(json.dumps(q, indent=2))
                v.facts["qor"] = {k: q.get(k) for k in ("global_timing", "utilization", "drc")}
                if q.get("drc"):
                    v.warn(f"check_routes: {q['drc']} DRC(s)")
        status = "fail" if not v.ok else ("warn" if v.warnings else "ok")
        set_step(run_dir, step, status=status, rc=res["rc"], outcome=res["outcome"], host=res["host"],
                 pid=res["pid"], reattached=res["reattached"], duration_s=res["elapsed_s"],
                 fatal=v.fatal, warnings=v.warnings, facts=v.facts, ended=now_iso())
        mark_consumed(run_dir, step, res["launch_id"] or attempt)

        if step != "build":
            log(f"cores: requested max_cores={ctx['max_cores']}, FC applied set_host_options -max_cores "
                f"{v.facts.get('cores_echo', '?')}, timer threads {v.facts.get('timer_threads', '?')} "
                f"(host {res['host']})")
        log(f"==== {step}: {status.upper()}  (rc={res['rc']}, {res['elapsed_s'] / 60:.1f} min on {res['host']})")
        for w in v.warnings:
            log(f"WARN  {w}")
        for f in v.fatal:
            log(f"FAIL  {f}")
        if not v.ok:
            tl = tool_log(ctx, step)
            raise AirflowFailException(
                f"{step} FAILED on {res['host']}:\n  - " + "\n  - ".join(v.fatal)
                + f"\nlog: {tl}\nlast lines:\n{_tail(tl)}")
        return {"mode": "run", "status": status, "rc": res["rc"]}

    def on_kill(self):
        """Stop the remote FC job ONLY when a person stopped the task on purpose.

        Airflow also SIGTERMs a task when its own host is too slow (failed heartbeats, expired
        tokens -- seen on mipscs82 2026-10-06) or when Airflow is shut down. Killing a 20 h FC job
        for that would be wrong. So the task's state in the Airflow DB decides:
          failed / success / skipped / removed / upstream_failed  -> 'Mark failed/success' -> kill
          anything else (still running, restarting after Clear) or DB unreadable -> leave the job;
          a Clear re-attaches to it, `bin/pdflow.sh kill <run_tag>` stops it explicitly.
        Airflow SIGKILLs this process ~5 s after SIGTERM, so the kill itself runs detached."""
        if not self._run:
            return
        fixed, ctx = self._run
        flag = Path(os.environ.get("AIRFLOW_HOME", "/nonexistent")) / SHUTDOWN_FLAG
        if flag.exists() and time.time() - flag.stat().st_mtime < 600:
            self.log.warning("Airflow is shutting down: leaving %s running for re-attach", self.step)
            return
        state = _ti_state(self._ti_id)
        if state not in USER_STOP_STATES:
            self.log.warning("task stopped by Airflow (task state %r, not a user 'Mark failed'): NOT killing the "
                             "%s job -- Clear the task to re-attach, or stop it with: bin/pdflow.sh kill %s",
                             state, self.step, ctx["run_tag"])
            return
        ref = job_ref(ctx["run_dir"], self.step)
        if not ref:
            self.log.warning("task marked %s but no pid known for %s yet", state, self.step)
            return
        self.log.warning("task marked %s by a user: stopping remote job %s:%s (TERM, KILL after 60 s)", state, *ref)
        try:
            lid = read_launch(ctx["run_dir"], self.step).get("launch_id")
            if lid:
                mark_consumed(ctx["run_dir"], self.step, lid)   # a Clear afterwards launches fresh
            remote.kill_detached(fixed, ref[0], ref[1], grace_s=60,
                                 log_path=str(Path(ctx["run_dir"]) / "logs" / f"{self.step}.kill.log"),
                                 status_prefix=str(Path(ctx["run_dir"]) / "status" / self.step))
        except Exception as e:                      # never let on_kill raise
            self.log.error("kill failed: %s", e)


class PdQorGate(ApprovalOperator):
    ui_color = "#fbe7c6"

    def __init__(self, *, gated_step: str, response_hours: float = 72, **kwargs):
        # The DAG injects its own `params` into every operator; the HITL form only wants ours
        # (HITLOperator filters DAG-level params out again by source).
        form = dict(kwargs.pop("params", None) or {})
        form["comment"] = Param(None, type=["null", "string"], title="Comment (optional)",
                                description="Saved in run.json and summary.md")
        super().__init__(
            subject=f"QoR review after {gated_step}",
            body="(filled in at run time)",
            fail_on_reject=True,
            response_timeout=timedelta(hours=response_hours),
            params=form,
            **kwargs)
        self.gated_step = gated_step

    @property
    def serialized_params(self):
        # The DAG merges its 11 trigger params into every task AFTER HITLOperator filtered them,
        # so without this the review form would show (and require) all of them.
        return {"comment": self.params.get_param("comment").serialize()}

    def _record(self, ctx, **fields):
        def _f(m):
            m.setdefault("gates", {})[self.gated_step] = {"time": now_iso(), "dag_run_id": ctx["dag_run_id"], **fields}
        update_manifest(ctx["run_dir"], _f)

    def execute(self, context):
        ctx = _ctx(context)
        _relock(ctx)
        need, why = gate_needed(ctx, self.gated_step)
        if not need:
            self.log.info("gate after %s not needed: %s -> auto-approve", self.gated_step, why)
            self._record(ctx, decision="auto-approved", reason=why)
            return {"chosen_options": ["Approve"], "auto": why}
        run_dir = Path(ctx["run_dir"])
        summaries = {}
        for s in ("compile", "clock_opt_cts", "clock_opt_opto"):
            f = run_dir / "qor" / f"{s}.json"
            if f.is_file():
                summaries[s] = json.loads(f.read_text())
        if self.gated_step not in summaries:
            rpt = Path(ctx["module_dir"]) / "rm_fc" / "rpts" / self.gated_step
            summaries[self.gated_step] = summarize_step(rpt, self.gated_step)
        m = read_manifest(run_dir)
        extra = [f"precheck: {w}" for w in m.get("precheck_warnings", [])]
        self.subject = f"[{ctx['run_tag']}] {ctx['module']}: continue after {self.gated_step}?"
        self.body = render_markdown(ctx, self.gated_step, summaries, m.get("steps", {}).get(self.gated_step, {}), extra)
        self._record(ctx, decision="pending")
        self.log.info("waiting for Approve/Reject (Airflow UI -> this task -> Required Actions)")
        return super().execute(context)

    def execute_complete(self, context, event):
        ctx = _ctx(context)
        chosen = (event.get("chosen_options") or ["?"])[0]
        user = event.get("responded_by_user")
        user = (user.get("name") or user.get("id")) if isinstance(user, dict) else user
        pi = dict(event.get("params_input") or {})
        if pi.get("comment") is None:
            pi["comment"] = ""
        event = {**event, "params_input": pi}
        comment = pi["comment"]
        try:
            ret = super().execute_complete(context, event)
        except HITLTimeoutError:
            self._record(ctx, decision="timeout", user=None)
            raise AirflowFailException(f"no answer at the gate after {self.gated_step} within the timeout") from None
        except HITLRejectException:
            self._record(ctx, decision="rejected", user=user, comment=comment)
            raise AirflowFailException(
                f"REJECTED after {self.gated_step} by {user}" + (f": {comment}" if comment else "")
                + f". Resume later with run_tag={ctx['run_tag']} and a later start_from.") from None
        self._record(ctx, decision="approved" if chosen == "Approve" else chosen, user=user, comment=comment)
        return ret
