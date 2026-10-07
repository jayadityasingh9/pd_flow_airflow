"""Read-only prechecks before anything is copied or launched (fail fast, in seconds)."""
from __future__ import annotations

import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import remote
from .config import STEPS, module_cfg, step_index
from .patch import check_specs, check_toggles, specs_from_fixed, toggles_from_fixed
from .runner import live_jobs
from .workspace import LOCK, verify_reusable


def _tcl_set(text: str, var: str) -> str | None:
    m = re.search(rf'^\s*set\s+{re.escape(var)}\s+"?([^";\n]*)"?', text, re.M)
    return m.group(1).strip() if m else None


def run_precheck(ctx: dict, fixed: dict, log=print, lock_owner_finished=None) -> dict:
    fatal: list[str] = []
    warn: list[str] = []
    mcfg = module_cfg(fixed, ctx["module"])
    src_mod = Path(fixed["rm_fc_cad_src"]) / ctx["module"]
    # new run: check the Perforce source; resume: check the run copy we will actually use
    mod = Path(ctx["module_dir"]) if ctx["is_resume"] else src_mod
    tech = mod / "block_files" / ctx["tech_dir"]

    def need(path, what, kind="e"):
        p = Path(path)
        ok = {"e": p.exists, "d": p.is_dir, "f": p.is_file,
              "x": lambda: p.is_file() and os.access(p, os.X_OK)}[kind]()
        if not ok:
            fatal.append(f"{what} not found: {p}")
        return ok

    # --- sources and tools
    need(fixed["mips_home"], "MIPS_HOME (Perforce workspace)", "d")
    need(fixed["rm_fc_cad_src"], "rm_fc_cad source", "d")
    need(mod, f"module dir {ctx['module']}", "d")
    need(tech, f"TECH_DIR block_files/{ctx['tech_dir']} for {ctx['module']}", "d")
    need(ctx["top_file"], "top RTL file", "f")
    need(fixed["build_py"], "build.py", "x")
    need(fixed["raven_env"], "raven source_env.csh", "f")
    need(fixed["module_init"], "Modules init (tcsh)", "f")
    for f in fixed["build_extra_f"]:
        need(f, "std-cell -f file", "f")
    for step in STEPS[1:]:
        need(mod / "scripts_block" / "rm_fc_scripts" / f"{step}.tcl", f"RM script {step}.tcl", "f")
    common = mod / "block_files" / "common"
    need(common / f"{ctx['design_name']}.func.sdc", "functional SDC", "f")
    need(common / f"{ctx['design_name']}.FC.read_design.tcl", "FC read-design script", "f")

    # --- design_setup / fc_setup content
    ds = tech / "design_setup.tcl"
    fcs = mod / "scripts_block" / "rm_setup" / "fc_setup.tcl"
    if need(ds, "design_setup.tcl", "f"):
        text = ds.read_text(errors="replace")
        defs = _tcl_set(text, "DEF_FLOORPLAN_FILES")
        for d in (defs or "").split():
            need(tech / d, "floorplan DEF", "f")
        for line in text.splitlines():
            if line.lstrip().startswith("#") or "/mips/" not in line:
                continue
            for tok in re.findall(r"(/mips/[^\s\"';}\])]+)", line.split("#")[0]):
                if "$" not in tok and "*" not in tok and not Path(tok).exists():
                    warn.append(f"design_setup.tcl references a missing path: {tok}")
    if need(fcs, "fc_setup.tcl", "f"):
        uf = _tcl_set(fcs.read_text(errors="replace"), "UNIFIED_FLOW")
        if uf != "true":
            fatal.append(f"fc_setup.tcl UNIFIED_FLOW={uf!r}: this flow assumes true (compile saves place_opt)")
    fatal += check_specs(mod, specs_from_fixed(fixed))
    fatal += check_toggles(mod, toggles_from_fixed(fixed))

    ov = Path(fixed["mips_home"]).parent / "filelists" / mcfg["filelist_override"]
    if ov.exists():
        warn.append(f"filelist override {ov} exists: the FC read script will use IT, not this run's build output")
    if ctx["build_config"] != mcfg["default_build_config"]:
        warn.append(f"build_config {ctx['build_config']} is not {ctx['module']}'s default "
                    f"({mcfg['default_build_config']}); the module's fixed DEF/SDC are still used")

    # --- run dir
    run_dir = Path(ctx["run_dir"])
    if not ctx["is_resume"]:
        if run_dir.exists():
            fatal.append(f"{run_dir} already exists: choose another run_tag (or resume with start_from > build)")
    else:
        lk = run_dir / LOCK
        if lk.exists():
            import json
            cur = json.loads(lk.read_text() or "{}")
            if cur.get("dag_run_id") != ctx["dag_run_id"] and lock_owner_finished \
                    and lock_owner_finished(cur.get("dag_run_id")):
                warn.append(f"lock of finished Airflow run {cur.get('dag_run_id')} will be taken over")
            elif cur.get("dag_run_id") != ctx["dag_run_id"]:
                fatal.append(f"run {ctx['run_tag']} is locked by Airflow run {cur.get('dag_run_id')} "
                             f"(if that run is finished/dead: rm {lk})")
        for step in STEPS[: step_index(ctx["start_from"])]:
            fatal += [f"resume: {p}" for p in verify_reusable(ctx, step)]

    # --- disk
    root = Path(fixed["runs_root"])
    probe_dir = root if root.exists() else root.parent
    free_gb = shutil.disk_usage(probe_dir).free / 2**30
    need_gb = fixed["min_free_disk_gb"][ctx["size"]]
    if free_gb < need_gb:
        fatal.append(f"only {free_gb:.0f} GB free on {probe_dir}; {ctx['module']} ({ctx['size']}) needs >= {need_gb} GB")

    # --- hosts: all 5 in parallel (for the table), then the chosen one in detail
    with ThreadPoolExecutor(max_workers=len(fixed["fc_hosts"])) as ex:
        probes = dict(zip(fixed["fc_hosts"], ex.map(lambda h: remote.host_probe(fixed, h), fixed["fc_hosts"])))
    log("pd hosts right now:")
    log(f"  {'host':10} {'cores':>5} {'load':>6} {'free RAM':>9} {'users':>5}")
    for h, d in probes.items():
        mark = "  <- chosen" if h == ctx["fc_host"] else ""
        if "error" in d:
            log(f"  {h:10} unreachable: {d['error'][:80]}{mark}")
        else:
            log(f"  {h:10} {d.get('nproc', '?'):>5} {d.get('load1', '?'):>6} "
                f"{str(d.get('mem_avail_gb', '?')) + ' GB':>9} {d.get('users', '?'):>5}{mark}")
    hp = probes[ctx["fc_host"]]
    if "error" in hp:
        fatal.append(f"chosen host {ctx['fc_host']} unreachable over ssh: {hp['error']}")
    else:
        if isinstance(hp.get("nproc"), int) and ctx["max_cores"] > hp["nproc"]:
            fatal.append(f"max_cores={ctx['max_cores']} > {hp['nproc']} cores on {ctx['fc_host']}")
        if isinstance(hp.get("load1"), float) and isinstance(hp.get("nproc"), int) and hp["load1"] > 0.75 * hp["nproc"]:
            warn.append(f"{ctx['fc_host']} is busy (load {hp['load1']} on {hp['nproc']} cores)")
        mem_need = fixed["min_free_mem_gb"][ctx["size"]]
        ok_hosts = sorted((h for h, d in probes.items() if "error" not in d and isinstance(d.get("mem_avail_gb"), int)
                           and d["mem_avail_gb"] >= mem_need and isinstance(d.get("nproc"), int)
                           and d["nproc"] >= ctx["max_cores"]),
                          key=lambda h: (probes[h].get("load1", 99) / max(1, probes[h]["nproc"]), -probes[h]["mem_avail_gb"]))
        if isinstance(hp.get("mem_avail_gb"), int) and hp["mem_avail_gb"] < mem_need:
            # corvi compile crashed (SIGSEGV) on a host short of RAM with swap full: refuse up front
            fatal.append(f"{ctx['fc_host']} has only {hp['mem_avail_gb']} GB RAM available; {ctx['module']} "
                         f"({ctx['size']}) needs >= {mem_need} GB (corvi compile reached 81 GB and was still growing)."
                         + (f" Pick {ok_hosts[0]} ({probes[ok_hosts[0]]['mem_avail_gb']} GB free, load "
                            f"{probes[ok_hosts[0]].get('load1')}/{probes[ok_hosts[0]]['nproc']})" if ok_hosts else ""))
        if hp.get("swap_total_gb") and hp.get("swap_free_gb") == 0:
            warn.append(f"{ctx['fc_host']}: swap is completely used -- the host is under memory pressure")
        fe = remote.fc_env_probe(fixed, ctx["fc_host"])
        if "error" in fe:
            fatal.append(f"FC environment probe on {ctx['fc_host']} failed: {fe['error']}")
        else:
            if not fe.get("fc_shell", "").endswith("/fc_shell"):
                fatal.append(f"'module load {fixed['fc_module']}' gives no fc_shell on {ctx['fc_host']} "
                             f"(got {fe.get('fc_shell')!r})")
            if fe.get("license") != "set":
                fatal.append(f"SNPSLMD_LICENSE_FILE not set after module load on {ctx['fc_host']}")
            log(f"FC on {ctx['fc_host']}: {fe.get('fc_shell')} (license env {fe.get('license')})")

    # a step of THIS run still running (e.g. the original DAG run was not cleared): never start a
    # second FC job on the same nlib
    from .scripts import SCRIPT_NAMES  # noqa: F401  (keeps the names in one place)
    for j in [j for j in live_jobs(fixed["runs_root"]) if j["run"] == ctx["run_tag"]]:
        try:
            a = remote.alive(fixed, j["host"], int(j["pid"]), ctx["run_dir"])
        except ValueError:
            a = False
        if a is True:
            fatal.append(f"step {j['step']} of run {ctx['run_tag']} is still running on {j['host']} "
                         f"(session {j['pid']}): Clear its original task, or kill it: bin/pdflow.sh kill {ctx['run_tag']}")
        elif a is None:
            fatal.append(f"cannot reach {j['host']} to check step {j['step']} of this run (session {j['pid']})")
    busy = [j for j in live_jobs(fixed["runs_root"]) if j["host"] == ctx["fc_host"] and j["run"] != ctx["run_tag"]]
    for j in busy:
        warn.append(f"another pdflow job is live on {ctx['fc_host']}: run {j['run']} step {j['step']} since {j['since']}")
    return {"fatal": fatal, "warnings": warn, "hosts": probes}
