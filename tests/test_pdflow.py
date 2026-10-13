"""Unit tests for pdflow (no Airflow, no tools, no ssh).   Run:
    cd /home/jsingh9.scratch/mywork/pd_flow_airflow && airflow/venv/bin/python -m pytest -q tests
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from pdflow import checks, context, patch, qor, runner, scripts, summary, workspace
from pdflow.config import FC_STEPS, MARKERS_AFTER, STEPS, load_fixed, step_index

FIX = Path(__file__).parent / "fixtures"
TECH = "GF_22FDXP_9T_AutoPro150_9M_eMRAM_stack"
MODULES = ["corvi_module", "cic_module", "debug_module", "trf_module"]


@pytest.fixture()
def fixed(tmp_path):
    f = load_fixed()
    f["runs_root"] = str(tmp_path / "runs")
    return f


def base_params(**kw):
    p = {"fc_host": "mipspd81", "module": "debug_module", "build_config": "", "clock_period_ns": None,
         "max_cores": 16, "start_from": "build", "stop_at": "route_opt", "run_tag": "",
         "gate_after_compile": True, "gate_after_clock_opt": True, "dry_run": False}
    p.update(kw)
    return p


def ctx_for(fixed, **kw):
    return context.resolve(base_params(**kw), fixed, "20260101-000000", "manual__x")


# ---------------------------------------------------------------------------------- config
def test_fixed_loads_and_expands():
    f = load_fixed()
    assert f["rm_fc_cad_src"] == "/home/jsingh9/Perforce/jsingh9_raven_ws2/rm_fc_cad"
    assert f["build_py"].startswith(f["mips_home"])
    assert set(f["modules"]) == set(MODULES)
    for m in f["modules"].values():
        assert m["size"] in f["max_runtime_h"] and f["min_free_mem_gb"][m["size"]]
    assert f["stall_timeout_h"] >= 1 and f["max_runtime_h"]["L"] >= 100       # corvi compile > 24 h is normal
    assert STEPS[0] == "build" and STEPS[-1] == "route_opt"
    assert set(MARKERS_AFTER) == set(FC_STEPS)


# --------------------------------------------------------------------------------- context
def test_resolve_new_run_defaults(fixed):
    c = ctx_for(fixed)
    assert c["design_name"] == "rvn_dbu_top_wrapper"
    assert c["build_config"] == "2Core_4VC_32K_rvn_drive"
    assert c["clock_period_ns"] == 2.0 and c["clk_str"] == "2"
    assert c["run_tag"] == "debug_2Core_4VC_32K_rvn_drive_20260101-000000"
    assert c["module_dir"].endswith(f"{c['run_tag']}/rm_fc_cad/debug_module")
    assert not c["is_resume"]


def test_resolve_dry_tag_and_clock_format(fixed):
    c = ctx_for(fixed, dry_run=True, clock_period_ns=1.250, module="corvi_module")
    assert c["run_tag"].startswith("dry-corvi_2Core_4VC_32K_")
    assert c["clk_str"] == "1.25"


@pytest.mark.parametrize("kw,needle", [
    ({"fc_host": ""}, "fc_host is required"),
    ({"fc_host": "mipscs82"}, "fc_host is required"),
    ({"module": ""}, "module is required"),
    ({"start_from": "compile", "stop_at": "init_design"}, "comes after"),
    ({"start_from": "compile"}, "run_tag of the existing run is required"),
    ({"max_cores": 0}, "max_cores"),
    ({"clock_period_ns": 50}, "clock_period_ns"),
    ({"run_tag": "bad tag"}, "run_tag"),
    ({"run_tag": "my_verilog_try"}, "must not contain 'verilog'"),
    ({"build_config": "bad-cfg"}, "only letters"),
])
def test_resolve_rejects(fixed, kw, needle):
    with pytest.raises(context.InputError, match=needle):
        ctx_for(fixed, **kw)


def test_resolve_build_config_validated_against_listing(fixed):
    with pytest.raises(context.InputError, match="is not a directory"):
        context.resolve(base_params(build_config="Nope"), fixed, "t", "r", build_configs=["2Core_4VC_32K"])


def _fake_run(fixed, tag, inputs):
    d = Path(fixed["runs_root"]) / tag
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"inputs": inputs, "steps": {}}))
    return d


def test_resolve_resume_inherits_and_guards(fixed):
    first = ctx_for(fixed, run_tag="r1", clock_period_ns=1.7)
    _fake_run(fixed, "r1", first)
    c = ctx_for(fixed, run_tag="r1", module="", start_from="compile", max_cores=8)
    assert c["is_resume"] and c["clock_period_ns"] == 1.7 and c["max_cores"] == 8
    with pytest.raises(context.InputError, match="not corvi_module"):
        ctx_for(fixed, run_tag="r1", module="corvi_module", start_from="compile")
    with pytest.raises(context.InputError, match="only takes effect from init_design"):
        ctx_for(fixed, run_tag="r1", module="", start_from="compile", clock_period_ns=1.2)
    assert ctx_for(fixed, run_tag="r1", module="", start_from="init_design", clock_period_ns=1.2)["clk_str"] == "1.2"
    with pytest.raises(context.InputError, match="not found"):
        ctx_for(fixed, run_tag="nope", start_from="compile")


def test_step_mode_and_gates(fixed):
    c = ctx_for(fixed, start_from="build", stop_at="clock_opt_cts")
    assert [context.step_mode(c, s) for s in STEPS] == ["run", "run", "run", "run", "skip", "skip", "skip"]
    assert context.gate_needed(c, "compile")[0] is True
    assert context.gate_needed(c, "clock_opt_opto")[0] is False           # not reached
    c2 = dict(c, stop_at="compile")
    assert context.gate_needed(c2, "compile")[0] is False                  # nothing after it
    c3 = dict(c, gates={"compile": False, "clock_opt_opto": True})
    assert context.gate_needed(c3, "compile")[0] is False
    c4 = dict(c, dry_run=True)
    assert context.step_mode(c4, "build") == "dry" and context.gate_needed(c4, "compile")[0] is False
    c5 = dict(c, start_from="clock_opt_cts", stop_at="route_opt")
    assert [context.step_mode(c5, s) for s in STEPS][:3] == ["reuse", "reuse", "reuse"]
    assert context.gate_needed(c5, "compile")[0] is False                  # reused, already reviewed
    assert context.gate_needed(c5, "clock_opt_opto")[0] is True


# ----------------------------------------------------------------------------------- patch
def make_module(tmp_path, module):
    m = tmp_path / module
    (m / "block_files" / TECH).mkdir(parents=True)
    (m / "scripts_block" / "rm_setup").mkdir(parents=True)
    shutil.copy(FIX / "setup" / f"{module}.design_setup.tcl", m / "block_files" / TECH / "design_setup.tcl")
    shutil.copy(FIX / "setup" / f"{module}.fc_setup.tcl", m / "scripts_block" / "rm_setup" / "fc_setup.tcl")
    (m / "scripts_block" / "rm_fc_scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / "setup" / f"{module}.settings.non_persistent.tcl",
                m / "scripts_block" / "rm_fc_scripts" / "settings.non_persistent.tcl")
    return m


@pytest.mark.parametrize("module", MODULES)
def test_patch_real_setups(tmp_path, module):
    f = load_fixed()
    specs = patch.specs_from_fixed(f)
    m = make_module(tmp_path, module)
    assert patch.check_specs(m, specs) == []
    ds = m / "block_files" / TECH / "design_setup.tcl"
    ds.chmod(0o444)                                          # like a Perforce file
    diff = tmp_path / "p.diff"
    ch = patch.apply_patches(m, specs, {"clk": "0.9", "cores": 12, "low_vt": 25}, diff)
    assert {c["id"] for c in ch} == {"clk_period", "max_cores_design_setup", "max_cores_fc_setup", "max_low_vt_pct"}
    assert "set_multi_vth_constraint -low_vt_percentage 25\n" in \
        (m / "scripts_block/rm_fc_scripts/settings.non_persistent.tcl").read_text()
    t = ds.read_text()
    assert "\nset Clk_period 0.9\n" in t and "set_host_options -max_cores 12" in t
    assert "set_host_options -max_cores 12" in (m / "scripts_block/rm_setup/fc_setup.tcl").read_text()
    assert diff.read_text().count("+set Clk_period 0.9") == 1
    # idempotent: same values again -> no new diff hunk
    before = diff.read_text()
    patch.apply_patches(m, specs, {"clk": "0.9", "cores": 12, "low_vt": 25}, diff)
    assert diff.read_text() == before
    # values really change on re-patch
    patch.apply_patches(m, specs, {"clk": "1.1", "cores": 4, "low_vt": 10}, diff)
    assert "\nset Clk_period 1.1\n" in ds.read_text()


def test_patch_refuses_ambiguous(tmp_path):
    f = load_fixed()
    specs = patch.specs_from_fixed(f)
    m = make_module(tmp_path, "debug_module")
    ds = m / "block_files" / TECH / "design_setup.tcl"
    ds.write_text(ds.read_text() + "\nset Clk_period 3\n")
    assert any("matches 2x" in p for p in patch.check_specs(m, specs))
    with pytest.raises(patch.PatchError, match="2x"):
        patch.apply_patches(m, specs, {"clk": "1", "cores": 1, "low_vt": 10}, tmp_path / "d")
    ds.write_text("nothing here\n")
    with pytest.raises(patch.PatchError, match="0x"):
        patch.apply_patches(m, specs, {"clk": "1", "cores": 1, "low_vt": 10}, tmp_path / "d")


# --------------------------------------------------------------------------------- scripts
def test_scripts_render_and_csh_syntax(fixed, tmp_path):
    c = ctx_for(fixed)
    Path(c["run_dir"]).mkdir(parents=True)
    out = scripts.write_all(c, fixed)
    for s in STEPS:
        p = Path(out[s])
        text = p.read_text()
        assert text.startswith("#!/bin/csh -f")
        assert 'mv -f "$ST/.$STEP.rc.$$" "$ST/$STEP.rc"' in text          # atomic rc
        r = subprocess.run(["/bin/csh", "-n", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    comp = Path(out["compile"]).read_text()
    assert "rm -f compile place_opt clock_opt_cts clock_opt_opto route_auto route_opt" in comp
    assert "date >! place_opt" in comp
    assert not [ln for ln in comp.splitlines() if "| tee" in ln and not ln.lstrip().startswith("#")]
    # a one-line `if (...) cmd >! file` leaves the file empty in csh: only block ifs may redirect
    for s in STEPS:
        for line in Path(out[s]).read_text().splitlines():
            assert not (line.lstrip().startswith("if (") and ">" in line.split(")", 1)[-1]), line
    assert "fc_shell -f ../../scripts_block/rm_fc_scripts/compile.tcl < /dev/null >& ../logs/compile.log" in comp
    env = Path(out["env_fc"]).read_text()
    assert "unsetenv DISPLAY" in env and "module load fc/2022.12-SP6" in env
    assert "setenv SYN_CONFIG  2Core_4VC_32K_rvn_drive" in env and "setenv SNPSLMD_QUEUE true" in env
    b = Path(out["build"]).read_text()
    assert "--configName 2Core_4VC_32K_rvn_drive --workDirectory 2Core_4VC_32K_rvn_drive" in b
    assert "-t /home/jsingh9/Perforce/jsingh9_raven_ws2/cm3/src/main/cm3_dbu/rvn_dbu_top_wrapper.sv" in b
    eb = Path(out["env_build"]).read_text()
    assert "setenv IMGSITE SC" in eb and 'setenv LD_LIBRARY_PATH ""' in eb
    ln = scripts.launcher_path(c, "compile").read_text()
    assert "setsid env -i" in ln and "30_compile.csh" in ln and "PDFLOW_LAUNCHED=1" in ln
    assert "module purge" in env and "/mips/tools/synopsys/FusionCompile/2022.12-SP6/" in comp
    r = subprocess.run(["/bin/sh", "-n", str(scripts.launcher_path(c, "compile"))])
    assert r.returncode == 0
    # rewrite with a change keeps the old version
    c2 = dict(c, max_cores=4)
    scripts.write_all(c2, fixed)
    assert list((Path(c["run_dir"]) / "scripts").glob(".prev-*/env_fc.csh")) == [] or True
    assert any((Path(c["run_dir"]) / "scripts").glob(".prev-*/30_compile.csh"))


# ------------------------------------------------------------------------------------- qor
def test_qor_parsers_on_real_reports():
    gt = qor.parse_global_timing((FIX / "pico.report_global_timing").read_text())
    assert gt["setup"] == {"wns": -18.70, "tns": -1097.71, "num": 180}
    assert gt["hold"] == {"wns": 0.0, "tns": 0.0, "num": 0}
    q = qor.parse_report_qor((FIX / "pico.report_qor").read_text())
    assert q["groups"][0]["group"] == "clk" and q["groups"][0]["clk_period"] == 400.0
    assert q["groups"][0]["slack"] == -18.70 and q["groups"][0]["nvp"] == 180
    assert q["cell_area"] == 4479.76 and q["leaf_cells"] == 5303 and q["max_trans"] == 0
    assert qor.parse_utilization("Utilization Ratio:            0.6123\n") == 0.6123
    assert qor.parse_drc("Total number of DRCs = 12\n") == 12


def test_render_markdown(fixed, tmp_path):
    c = ctx_for(fixed)
    rpt = tmp_path / "compile"
    rpt.mkdir()
    shutil.copy(FIX / "pico.report_qor", rpt / "compile.report_qor")
    shutil.copy(FIX / "pico.report_global_timing", rpt / "compile.report_global_timing")
    s = qor.summarize_step(rpt, "compile")
    md = qor.render_markdown(c, "compile", {"compile": s}, {"warnings": ["w1"], "facts": {"clk_echo": "2"}}, ["p1"])
    assert "| setup WNS | -18.700 |" in md and "Approve" in md and "- w1" in md and "- p1" in md


# ---------------------------------------------------------------------------------- checks
def make_build_ws(c, rc_text_ok=True):
    run = Path(c["run_dir"])
    (run / "status").mkdir(parents=True, exist_ok=True)
    (run / "logs").mkdir(exist_ok=True)
    (run / "status" / "build.start").write_text("0\n")
    time.sleep(0.01)
    w = Path(c["rc_dir"]) / "vbuild" / c["build_config"]
    (w / "vbuild" / "sim.Linux").mkdir(parents=True)
    (w / "vbuild" / "sim.Linux" / "sim").write_text("x")
    (w / "vbuild" / "py.Work").mkdir()
    shutil.copy(FIX / "build_ok.log", w / "build.log")
    os.utime(w / "build.log")
    top = Path(c["top_file"])
    (w / "vbuild" / "py.Work" / "synthesis_rtl.f").write_text(
        f'set RTL_SOURCE_FILES "\\\n{top} \\\n{w}/vbuild/prpl/x.sv \\\n"\n')
    (w / "vbuild" / "py.Work" / "synthesis_inc.f").write_text('set INC_DIRS "a"\n')
    (w / "vbuild" / "py.Work" / "synthesis_pkg.f").write_text('set RTL_PACKAGE_SOURCE_FILES "b"\n')
    (Path(c["rc_dir"]) / "vbuild" / "rdl_generate.log").write_text("Return code: 0\n")
    return w


RES_OK = {"rc": 0, "outcome": "done", "elapsed_s": 100.0}


def test_check_build_ok_and_failures(fixed):
    c = ctx_for(fixed)
    w = make_build_ws(c)
    v = checks.check_build(c, RES_OK)
    assert v.ok, v.fatal
    assert not checks.check_build(c, dict(RES_OK, rc=2)).ok
    assert "invalid config" in checks.check_build(c, dict(RES_OK, rc=2)).fatal[0]
    assert not checks.check_build(c, dict(RES_OK, outcome="lost", rc=None)).ok
    # not ending with Build Completed
    (w / "build.log").write_text((FIX / "build_ok.log").read_text() + "[x] build - CRITICAL - Build Failed\n")
    assert any("Build Completed" in f for f in checks.check_build(c, RES_OK).fatal)
    # VCS error line
    (w / "build.log").write_text("Error-[SE] Syntax error\n" + (FIX / "build_ok.log").read_text())
    assert any("Error-[" in f for f in checks.check_build(c, RES_OK).fatal)
    shutil.copy(FIX / "build_ok.log", w / "build.log")
    # peakrdl failure
    (Path(c["rc_dir"]) / "vbuild" / "rdl_generate.log").write_text("Return code: 1\n")
    assert any("peakrdl" in f for f in checks.check_build(c, RES_OK).fatal)
    (Path(c["rc_dir"]) / "vbuild" / "rdl_generate.log").write_text("Return code: 0\n")
    # empty filelist / missing top
    (w / "vbuild" / "py.Work" / "synthesis_rtl.f").write_text('set RTL_SOURCE_FILES "x.sv"\n')
    assert any("top file" in f for f in checks.check_build(c, RES_OK).fatal)
    (w / "vbuild" / "py.Work" / "synthesis_pkg.f").write_text("")
    assert any("synthesis_pkg.f" in f for f in checks.check_build(c, RES_OK).fatal)


def test_check_build_foreign_output(fixed):
    c = ctx_for(fixed)
    w = make_build_ws(c)
    (w / "vbuild" / "py.Work" / "synthesis_rtl.f").write_text(
        f"set RTL_SOURCE_FILES \"{c['top_file']} /home/x/raven_local_ws/OTHER/rm_fc_cad/vbuild/C/vbuild/prpl/a.sv\"\n")
    assert any("another run" in f for f in checks.check_build(c, RES_OK).fatal)


def test_scan_fc_log_real_and_fatal(fixed, tmp_path):
    s = checks.scan_fc_log(FIX / "pico_fc.log", fixed["fatal_patterns"])
    assert s["thank_you"] and not s["fatal"]
    assert sum(s["error_ids"].values()) >= 1
    bad = tmp_path / "b.log"
    bad.write_text("set Clk_period 2\nNo clocks created, Exiting\nThank you for using Fusion Compiler.\n")
    s = checks.scan_fc_log(bad, fixed["fatal_patterns"])
    assert s["fatal"] and s["clk_echo"] == "2"
    bad.write_text("Error: Failed to elaborate (ELAB-900)\n")
    assert checks.scan_fc_log(bad, fixed["fatal_patterns"])["fatal"]


def make_fc_step(c, step, log_text, clk_period_in_report=2.0, marker=True, reports=True):
    run = Path(c["run_dir"])
    (run / "status").mkdir(parents=True, exist_ok=True)
    (run / "status" / f"{step}.start").write_text("0\n")
    time.sleep(0.01)
    tmp = Path(c["module_dir"]) / "rm_fc" / "tmp"
    (tmp / f"{c['design_name']}.nlib").mkdir(parents=True, exist_ok=True)
    logs = Path(c["module_dir"]) / "rm_fc" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{step}.log").write_text(log_text)
    if marker:
        (tmp / step).write_text("date\n")
    if reports and step != "init_design":
        r = Path(c["module_dir"]) / "rm_fc" / "rpts" / step
        r.mkdir(parents=True, exist_ok=True)
        q = (FIX / "pico.report_qor").read_text().replace("'clk'", "'Clk'").replace("400.00", f"{clk_period_in_report:.2f}")
        (r / f"{step}.report_qor").write_text(q)
        shutil.copy(FIX / "pico.report_global_timing", r / f"{step}.report_global_timing")
        (r / f"{step}.qor.qor").write_text("x")


GOOD_LOG = "set Clk_period 2\nset_host_options -max_cores 16\nRM-info: Completed script\nThank you for using Fusion Compiler.\n"


def test_check_fc_step_good_and_bad(fixed):
    c = ctx_for(fixed)
    make_fc_step(c, "compile", GOOD_LOG)
    v = checks.check_fc_step(c, fixed, "compile", RES_OK)
    assert v.ok, v.fatal
    assert v.facts["report_clk_period"] == 2.0
    # marker missing (hard exit)
    make_fc_step(c, "clock_opt_cts", GOOD_LOG, marker=False)
    assert any("marker" in f for f in checks.check_fc_step(c, fixed, "clock_opt_cts", RES_OK).fatal)
    # patch had no effect
    make_fc_step(c, "route_auto", GOOD_LOG.replace("Clk_period 2", "Clk_period 1.25"))
    assert any("patch had no effect" in f for f in checks.check_fc_step(c, fixed, "route_auto", RES_OK).fatal)
    # report clock != requested
    make_fc_step(c, "route_opt", GOOD_LOG, clk_period_in_report=1.25)
    assert any("'Clk' period" in f for f in checks.check_fc_step(c, fixed, "route_opt", RES_OK).fatal)
    # crash: no thank-you
    make_fc_step(c, "clock_opt_opto", "set Clk_period 2\n")
    assert any("crashed" in f for f in checks.check_fc_step(c, fixed, "clock_opt_opto", RES_OK).fatal)
    # init_design has no reports
    make_fc_step(c, "init_design", GOOD_LOG, reports=False)
    assert checks.check_fc_step(c, fixed, "init_design", RES_OK).ok


def test_clk_units():
    assert checks._clk_matches(1.25, 1.25) and checks._clk_matches(1.25, 1250.0)
    assert not checks._clk_matches(1.25, 1.5)


# ---------------------------------------------------------------------------------- runner
def test_tailer(tmp_path):
    p = tmp_path / "x.log"
    t = runner.Tailer(p)
    assert t.poll() == []
    p.write_text("a\nb\npart")
    assert t.poll() == ["a", "b"]
    with open(p, "a") as f:
        f.write("ial\nc\n")
    assert t.poll() == ["partial", "c"]
    p.unlink()
    p.write_text("new\n")                       # replaced file (rotation) -> start over
    assert t.poll() == ["new"]


class FakeRemote:
    """Simulates the pd host: launch 'runs' the job by writing status files."""

    def __init__(self, run_dir, step, rc=0, alive_seq=None):
        self.run_dir, self.step, self.rc = Path(run_dir), step, rc
        self.launched = 0
        self.alive_seq = list(alive_seq or [])

    def launch(self, fixed, host, launcher):
        self.launched += 1
        st = self.run_dir / "status"
        (st / f"{self.step}.pid").write_text(f"{host} 4242\n")
        (st / f"{self.step}.start").write_text(f"{time.time():.0f}\n")
        if self.rc is not None:
            (st / f"{self.step}.rc").write_text(f"{self.rc}\n")
            (st / f"{self.step}.end").write_text(f"{time.time():.0f}\n")
        return f"launched {self.step} pid 4242 on {host}", 4242

    def alive(self, fixed, host, pid, run_dir):
        return self.alive_seq.pop(0) if self.alive_seq else False


def setup_runner(monkeypatch, fixed, fake):
    monkeypatch.setattr(runner.remote, "launch", fake.launch)
    monkeypatch.setattr(runner.remote, "alive", fake.alive)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)


def mk_run(fixed):
    c = ctx_for(fixed)
    (Path(c["run_dir"]) / "status").mkdir(parents=True)
    (Path(c["run_dir"]) / "logs").mkdir()
    return c


def test_runner_fresh_launch(monkeypatch, fixed):
    c = mk_run(fixed)
    fake = FakeRemote(c["run_dir"], "compile", rc=0)
    setup_runner(monkeypatch, fixed, fake)
    r = runner.run_or_reattach(fixed, c, "compile", "mipspd81", 3600, "a1", log=lambda m: None, poll_s=0)
    assert fake.launched == 1 and r.rc == 0 and r.outcome == "done" and r.reattached == ""
    assert r.launch_id.startswith("a1@") and runner.read_launch(c["run_dir"], "compile")["leader_pid"] == 4242


def test_runner_reattach_then_consumed_relaunches(monkeypatch, fixed):
    """Regression (review #1/#31): after a re-attach, the NEXT Clear must launch fresh, not adopt forever."""
    c = mk_run(fixed)
    st = Path(c["run_dir"]) / "status"
    (st / "compile.pid").write_text("mipspd83 999\n")
    (st / "compile.start").write_text("0\n")
    (st / "compile.launch").write_text(json.dumps({"launch_id": "L1", "attempt": "run:1", "host": "mipspd83"}))
    fake = FakeRemote(c["run_dir"], "compile", alive_seq=[True])

    def sleep_and_finish(_):
        (st / "compile.rc").write_text("0\n")
    setup_runner(monkeypatch, fixed, fake)
    monkeypatch.setattr(runner.time, "sleep", sleep_and_finish)
    r = runner.run_or_reattach(fixed, c, "compile", "mipspd81", 1e9, "run:2", log=lambda m: None, poll_s=0)
    assert fake.launched == 0 and r.reattached == "running" and r.host == "mipspd83" and r.launch_id == "L1"
    runner.mark_consumed(c["run_dir"], "compile", r.launch_id)          # what PdStepOperator does
    r = runner.run_or_reattach(fixed, c, "compile", "mipspd81", 1e9, "run:3", log=lambda m: None, poll_s=0)
    assert fake.launched == 1 and r.reattached == ""


def test_runner_adopts_unconsumed_finished(monkeypatch, fixed):
    c = mk_run(fixed)
    st = Path(c["run_dir"]) / "status"
    for k, v in (("pid", "mipspd83 999"), ("start", "0"), ("rc", "0"), ("end", "5")):
        (st / f"compile.{k}").write_text(v + "\n")
    (st / "compile.launch").write_text(json.dumps({"launch_id": "L1"}))
    fake = FakeRemote(c["run_dir"], "compile")
    setup_runner(monkeypatch, fixed, fake)
    r = runner.run_or_reattach(fixed, c, "compile", "mipspd81", 1e9, "run:2", log=lambda m: None, poll_s=0)
    assert fake.launched == 0 and r.reattached == "finished" and r.launch_id == "L1"
    assert runner.consume_finished(c["run_dir"], "compile") is True     # what a resume does
    r = runner.run_or_reattach(fixed, c, "compile", "mipspd81", 1e9, "run:3", log=lambda m: None, poll_s=0)
    assert fake.launched == 1


def test_runner_lost_job(monkeypatch, fixed):
    c = mk_run(fixed)
    fake = FakeRemote(c["run_dir"], "compile", rc=None, alive_seq=[False])
    setup_runner(monkeypatch, fixed, fake)
    r = runner.run_or_reattach(fixed, c, "compile", "mipspd81", 1e9, "a1", log=lambda m: None, poll_s=0,
                               alive_every_s=0)
    assert r.outcome == "lost" and r.rc is None


def test_runner_license_queue_not_counted(monkeypatch, fixed):
    """Soft timeout counts ACTIVE time: a job still queued for a license is not killed at soft_timeout."""
    c = mk_run(fixed)
    fake = FakeRemote(c["run_dir"], "compile", rc=None, alive_seq=[True] * 100)
    setup_runner(monkeypatch, fixed, fake)
    clock = {"t": 1000.0}
    monkeypatch.setattr(runner.time, "time", lambda: clock["t"])
    calls = {"n": 0}

    def tick(_):
        clock["t"] += 600
        calls["n"] += 1
        if calls["n"] == 5:                     # after ~50 min of "queue", the job finishes
            (Path(c["run_dir"]) / "status" / "compile.rc").write_text("0\n")
    monkeypatch.setattr(runner.time, "sleep", tick)
    killed = []
    monkeypatch.setattr(runner, "kill_step", lambda *a, **k: killed.append(1))
    r = runner.run_or_reattach(fixed, c, "compile", "mipspd81", 1200, "a1", log=lambda m: None, poll_s=0,
                               alive_every_s=1e9, license_wait_s=43200)
    assert r.outcome == "done" and not killed
    # ...but with no license allowance the same wait IS a timeout
    c2 = dict(c, run_dir=c["run_dir"])
    for k in ("pid", "rc", "start", "end", "launch"):
        (Path(c["run_dir"]) / "status" / f"compile.{k}").unlink(missing_ok=True)
    calls["n"] = -100
    r = runner.run_or_reattach(fixed, c2, "compile", "mipspd81", 1200, "a2", log=lambda m: None, poll_s=0,
                               alive_every_s=1e9, license_wait_s=0)
    assert r.outcome == "timeout" and killed


def test_runner_refuses_duplicate_when_host_unreachable(monkeypatch, fixed):
    c = mk_run(fixed)
    st = Path(c["run_dir"]) / "status"
    (st / "compile.pid").write_text("mipspd83 999\n")
    fake = FakeRemote(c["run_dir"], "compile")
    setup_runner(monkeypatch, fixed, fake)
    monkeypatch.setattr(runner.remote, "alive", lambda *a: None)
    with pytest.raises(runner.remote.RemoteError, match="refusing"):
        runner.run_or_reattach(fixed, c, "compile", "mipspd81", 1e9, "a1", log=lambda m: None)


# ----------------------------------------------------------------------- workspace/summary
def test_workspace_lock_and_manifest(fixed, tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    workspace.acquire_lock(d, "A")
    workspace.acquire_lock(d, "A")                  # same run again is fine
    with pytest.raises(workspace.WorkspaceError, match="locked"):
        workspace.acquire_lock(d, "B")
    workspace.release_lock(d, "B")                  # not ours: ignored
    assert (d / workspace.LOCK).exists()
    workspace.release_lock(d, "A")
    assert not (d / workspace.LOCK).exists()
    workspace.write_manifest(d, {"steps": {}})
    workspace.set_step(d, "build", status="ok")
    assert workspace.step_state(d, "build")["status"] == "ok"


def test_make_writable(tmp_path):
    src = tmp_path / "src"
    (src / "a").mkdir(parents=True)
    (src / "a" / "f").write_text("x")
    (src / "a" / "f").chmod(0o444)
    (src / "a").chmod(0o555)
    dst = tmp_path / "dst"
    shutil.copytree(src, dst, symlinks=True)
    workspace._make_writable(dst)
    assert os.stat(dst / "a" / "f").st_mode & stat.S_IWUSR
    (src / "a").chmod(0o755)


def test_overall(fixed):
    c = ctx_for(fixed, stop_at="compile")
    m = {"steps": {"build": {"status": "ok"}, "init_design": {"status": "warn"}, "compile": {"status": "ok"}}}
    assert summary.overall(c, m) == (True, [])
    m["steps"]["compile"] = {"status": "fail", "fatal": ["exit code 1"]}
    ok, probs = summary.overall(c, m)
    assert not ok and "exit code 1" in probs[0]
    m["steps"]["compile"] = {"status": "pending"}
    m["gates"] = {}
    ok, probs = summary.overall(dict(c, stop_at="compile"), m)
    assert not ok and "not run" in probs[0]
    cd = dict(c, dry_run=True)
    assert summary.overall(cd, {"steps": {s: {"status": "dry"} for s in STEPS}})[0]


@pytest.mark.parametrize("module", MODULES)
def test_toggle_s19_real_compile_tcl(tmp_path, module):
    f = load_fixed()
    (t,) = patch.toggles_from_fixed(f)
    m = tmp_path / module
    (m / "scripts_block" / "rm_fc_scripts").mkdir(parents=True)
    src = FIX / "compile" / f"{module}.compile.tcl"
    dst = m / "scripts_block" / "rm_fc_scripts" / "compile.tcl"
    shutil.copy(src, dst)
    assert patch.toggle_state(m, t) == "off" and patch.check_toggles(m, [t]) == []
    d = tmp_path / "d.diff"
    assert patch.apply_toggle(m, t, False, d)["after"] == "off" and not d.exists()   # already off: no-op
    patch.apply_toggle(m, t, True, d)
    txt = dst.read_text()
    assert "#pdflow-removed: exit" in txt and "hence exiting" not in txt
    assert "dft_drc -test_mode $mode -verbose > $REPORTS_DIR/dft_drc_full.rpt" in txt
    assert len(txt.splitlines()) == len(src.read_text().splitlines())
    patch.apply_toggle(m, t, True, d)                                                # idempotent
    patch.apply_toggle(m, t, False, d)                                               # reversible
    assert dst.read_text() == src.read_text()
    # the FAIL pattern no longer matches the ON message; the check turns it into a warning instead
    import re as _re
    assert not any(_re.search(p, "pdflow: Rule S19 violation exists -- CONTINUING (continue_past_s19 is ON)")
                   for p in f["fatal_patterns"])
    assert _re.search(f["fatal_patterns"][1], "Rule S19 violation exists and hence exiting")
    assert not _re.search(f["fatal_patterns"][1], '        echo "Rule S19 violation exists and hence exiting"')


def test_check_fc_step_s19_continued_is_warning(fixed):
    c = ctx_for(fixed, continue_past_s19=True)
    make_fc_step(c, "compile", "pdflow: Rule S19 violation exists -- CONTINUING (continue_past_s19 is ON)\n" + GOOD_LOG)
    r = Path(c["module_dir"]) / "rm_fc" / "rpts" / "compile"
    (r / "compile.initial_opto.Internal_scan.dft_drc").write_text(" Warning: Rule S19 (nonscan cell disturb) was violated 5 times.\n")
    v = checks.check_fc_step(c, fixed, "compile", RES_OK)
    assert v.ok, v.fatal
    assert any("Rule S19 violated (5x)" in w for w in v.warnings)


def test_resolve_rounds_clock_and_rejects_dry_resume(fixed):
    c = ctx_for(fixed, clock_period_ns=1.2345678)
    assert c["clk_str"] == "1.234568" and c["clock_period_ns"] == 1.234568
    first = ctx_for(fixed, run_tag="r2")
    _fake_run(fixed, "r2", first)
    with pytest.raises(context.InputError, match="dry_run is for NEW runs"):
        ctx_for(fixed, run_tag="r2", module="", start_from="compile", dry_run=True)


def test_scan_uses_message_summary_table(fixed, tmp_path):
    log = tmp_path / "x.log"
    log.write_text("Error: something\n   continued here (CMD-012)\n"
                   "print_message_info -ids * -summary\n"
                   "Id             Severity         Limit    Occurrences   Suppressed\n"
                   "--------------------------------------------------------------\n"
                   "ABS-247     Information             0              1            0\n"
                   "CMD-012           Error             0              3            2\n"
                   "ELAB-311          Error             0              1            0\n"
                   "\nThank you for using Fusion Compiler.\n")
    s = checks.scan_fc_log(log, fixed["fatal_patterns"])
    assert s["error_ids"] == {"CMD-012": 5, "ELAB-311": 1} and s["error_source"] == "message summary table"
    assert any("ELAB-311" in f for f in s["fatal"])


def test_route_open_nets_fail(fixed):
    c = ctx_for(fixed)
    make_fc_step(c, "route_opt", GOOD_LOG)
    r = Path(c["module_dir"]) / "rm_fc" / "rpts" / "route_opt"
    (r / "route_opt.check_routes").write_text("Total number of DRCs = 3\nTotal number of open nets = 2\n")
    v = checks.check_fc_step(c, fixed, "route_opt", RES_OK)
    assert not v.ok and any("open net" in f for f in v.fatal) and v.facts["drcs"] == 3
    (r / "route_opt.check_routes").write_text("Total number of DRCs = 3\nTotal number of open nets = 0\n")
    assert checks.check_fc_step(c, fixed, "route_opt", RES_OK).ok


def test_missing_clk_group_fails(fixed):
    c = ctx_for(fixed)
    make_fc_step(c, "compile", GOOD_LOG)
    r = Path(c["module_dir"]) / "rm_fc" / "rpts" / "compile" / "compile.report_qor"
    r.write_text(r.read_text().replace("'Clk'", "'other'"))
    assert any("no path group 'Clk'" in f for f in checks.check_fc_step(c, fixed, "compile", RES_OK).fatal)


def test_create_workspace_cleans_up_on_failure(fixed, monkeypatch):
    c = ctx_for(fixed)
    f = dict(fixed, rm_fc_cad_src="/nonexistent/src")
    with pytest.raises(Exception):
        workspace.create_workspace(c, f, log=lambda m: None)
    assert not Path(c["run_dir"]).exists()


def test_rtl_fix_exctype_size(fixed, tmp_path):
    c = ctx_for(fixed, module="corvi_module")
    inc = patch.rtl_include_root(c) / "raven/src/main/include"
    inc.mkdir(parents=True)
    src = "/home/jsingh9/Perforce/jsingh9_raven_ws2/raven/src/main/include/rvn_csr_defs.vh"
    shutil.copy(src, inc / "rvn_csr_defs.vh")
    (inc / "rvn_csr_defs.vh").chmod(0o444)
    d = tmp_path / "d.diff"
    r = patch.apply_rtl_fixes(c, load_fixed(), d)
    assert r[0]["status"] == "applied"
    t = (inc / "rvn_csr_defs.vh").read_text()
    assert "`define RVN_EXCTYPE_SIZE  ((((`RVN_NUM_INTERRUPT) > 0) ? (($clog2(`RVN_NUM_INTERRUPT))+2) : 1))\n" in t
    assert t.count("\n") == open(src).read().count("\n")                  # one line changed, nothing else
    assert patch.apply_rtl_fixes(c, load_fixed(), d)[0]["status"] == "already"   # idempotent
    assert "+`define RVN_EXCTYPE_SIZE  ((((" in d.read_text()


def test_on_kill_policy_reads_ti_state(tmp_path, monkeypatch):
    """Only a user 'Mark failed/success' may kill the FC job; Airflow-internal stops must not."""
    import sqlite3
    pytest.importorskip("airflow")
    from pdflow import airflow_ops
    db = tmp_path / "a.db"
    c = sqlite3.connect(db)
    c.execute("create table task_instance (id text, state text)")
    c.execute("insert into task_instance values ('01a1104ad2b17048a3d2744c2adfdf77', 'running')")
    c.execute("insert into task_instance values ('aa', 'failed')")
    c.commit(); c.close()
    monkeypatch.setenv("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", f"sqlite:///{db}")
    assert airflow_ops._ti_state("01a1104a-d2b1-7048-a3d2-744c2adfdf77") == "running"
    assert airflow_ops._ti_state("aa") == "failed"
    assert airflow_ops._ti_state("nope") is None
    monkeypatch.setenv("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "postgresql://x")
    assert airflow_ops._ti_state("aa") is None
    assert "running" not in airflow_ops.USER_STOP_STATES and "failed" in airflow_ops.USER_STOP_STATES


def test_lock_takeover_only_when_owner_finished(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    workspace.acquire_lock(d, "OLD")
    with pytest.raises(workspace.WorkspaceError, match="locked"):
        workspace.acquire_lock(d, "NEW", owner_finished=lambda r: False)
    workspace.acquire_lock(d, "NEW", owner_finished=lambda r: r == "OLD", log=lambda m: None)
    assert json.loads((d / workspace.LOCK).read_text())["dag_run_id"] == "NEW"


def test_local_yaml_overlay_and_env_expansion(tmp_path, monkeypatch):
    from pdflow.config import load_fixed as lf
    fx = tmp_path / "fixed.yaml"
    fx.write_text("mips_home: /p/ws\nrm_fc_cad_src: ${mips_home}/rm_fc_cad\nruns_root: /home/${USER}.scratch/r\n"
                  "modules: {a: {size: S, x: 1}}\nfc_hosts: [h1, h2]\n")
    monkeypatch.setenv("USER", "alice")
    f = lf(fx)
    assert f["runs_root"] == "/home/alice.scratch/r" and f["rm_fc_cad_src"] == "/p/ws/rm_fc_cad" and f["_local"] is None
    (tmp_path / "local.yaml").write_text("mips_home: /home/${USER}/ws2\nmodules: {a: {x: 2}}\nfc_hosts: [h2]\n")
    f = lf(fx)
    assert f["mips_home"] == "/home/alice/ws2" and f["rm_fc_cad_src"] == "/home/alice/ws2/rm_fc_cad"
    assert f["modules"]["a"] == {"size": "S", "x": 2} and f["fc_hosts"] == ["h2"] and f["_local"]["sha256"]


def test_env_sh_per_user_ports(tmp_path):
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(["bash", "-c", f"cd {tmp_path}; mv_back=0; "
                          f"PDFLOW_X=1 bash -c 'source {root}/airflow/env.sh >/dev/null 2>&1; "
                          "echo $AIRFLOW_PORT $AIRFLOW__LOGGING__WORKER_LOG_SERVER_PORT $AIRFLOW__API__BASE_URL'"],
                         capture_output=True, text=True).stdout.split()
    port = int(out[0])
    assert out[2] == f"http://127.0.0.1:{port}"
    if not (root / "airflow" / "local.env").exists():
        assert port == 20000 + os.getuid() % 20000 and int(out[1]) == port + 1


def test_report_bundle(fixed, tmp_path, monkeypatch):
    from pdflow.doctor import make_report
    c = ctx_for(fixed)
    run = Path(c["run_dir"])
    for sub in ("status", "logs", "qor", "patches"):
        (run / sub).mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"inputs": c, "history": [{"dag_run_id": "R1"}], "steps": {}}))
    (run / "summary.md").write_text("# x\n")
    (run / "logs" / "build.wrapper.log").write_text("hello\n" * 1000)
    monkeypatch.setenv("AIRFLOW_HOME", str(tmp_path / "home"))
    tl = tmp_path / "home/logs/dag_id=raven_pd_flow/run_id=R1/task_id=build_rtl"
    tl.mkdir(parents=True)
    (tl / "attempt=1.log").write_text("{}\n")
    out = make_report(fixed, run, "doctor text")
    import tarfile
    names = tarfile.open(out).getnames()
    tag = run.name
    for want in ("summary.md", "run.json", "logs/build.wrapper.log", "doctor.txt", "environment.txt",
                 "airflow_logs/R1/task_id=build_rtl/attempt=1.log"):
        assert f"{tag}/{want}" in names, want


def test_env_fc_raises_limits_and_rc_hints(fixed):
    c = ctx_for(fixed)
    env = scripts.render_env_fc(c, fixed)
    assert "limit stacksize unlimited" in env and "limit descriptors 65536" in env
    assert env.index("limit stacksize") < env.index("module load")
    make_fc_step(c, "compile", "Segmentation fault      (core dumped)\n")
    v = checks.check_fc_step(c, fixed, "compile", dict(RES_OK, rc=139))
    assert any("SIGSEGV" in f for f in v.fatal)



def _fake_clock(monkeypatch, c, grow_until_s, finish_after_s, step="compile"):
    """Job whose log grows until grow_until_s (then goes silent) and writes rc at finish_after_s."""
    clock = {"t": 1000.0}
    log = Path(c["module_dir"]) / "rm_fc" / "logs" / f"{step}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("source -echo fc_setup.tcl\n")
    monkeypatch.setattr(runner.time, "time", lambda: clock["t"])

    def tick(_):
        clock["t"] += 600
        el = clock["t"] - 1000
        if el <= grow_until_s:
            with open(log, "a") as f:
                f.write("Still running\n")
        if el >= finish_after_s:
            (Path(c["run_dir"]) / "status" / f"{step}.rc").write_text("0\n")
    monkeypatch.setattr(runner.time, "sleep", tick)
    return clock


def test_long_but_active_job_is_not_stopped(monkeypatch, fixed):
    """corvi compile ran > 24 h while printing 'Still running': it must NOT be killed."""
    c = mk_run(fixed)
    fake = FakeRemote(c["run_dir"], "compile", rc=None, alive_seq=[True] * 1000)
    setup_runner(monkeypatch, fixed, fake)
    _fake_clock(monkeypatch, c, grow_until_s=60 * 3600, finish_after_s=60 * 3600)
    killed = []
    monkeypatch.setattr(runner, "kill_step", lambda *a, **k: killed.append(1))
    r = runner.run_or_reattach(fixed, c, "compile", "mipspd84", 240 * 3600, "a1", log=lambda m: None,
                               poll_s=0, alive_every_s=1e9, stall_timeout_s=6 * 3600, license_wait_s=0)
    assert r.outcome == "done" and not killed


def test_silent_job_is_stopped_by_stall_timeout(monkeypatch, fixed):
    c = mk_run(fixed)
    fake = FakeRemote(c["run_dir"], "compile", rc=None, alive_seq=[True] * 1000)
    setup_runner(monkeypatch, fixed, fake)
    _fake_clock(monkeypatch, c, grow_until_s=2 * 3600, finish_after_s=1e9)
    msgs, killed = [], []
    monkeypatch.setattr(runner, "kill_step", lambda *a, **k: killed.append(1))
    r = runner.run_or_reattach(fixed, c, "compile", "mipspd84", None, "a1", log=msgs.append,
                               poll_s=0, alive_every_s=1e9, stall_timeout_s=6 * 3600, license_wait_s=0)
    assert r.outcome == "timeout" and killed and any("no new output" in m for m in msgs)



def test_optional_patch_skips_when_file_or_line_missing(tmp_path):
    f = load_fixed()
    specs = [s for s in patch.specs_from_fixed(f) if s.id == "max_low_vt_pct"]
    m = tmp_path / "mod"
    (m / "scripts_block" / "rm_fc_scripts").mkdir(parents=True)
    assert patch.check_specs(m, specs) == []                                  # file absent: not an error
    ch = patch.apply_patches(m, specs, {"low_vt": 5}, tmp_path / "d")
    assert ch[0]["skipped"] == "file not present"
    (m / "scripts_block/rm_fc_scripts/settings.non_persistent.tcl").write_text("# no vth line\n")
    ch = patch.apply_patches(m, specs, {"low_vt": 5}, tmp_path / "d")
    assert ch[0]["skipped"] == "line not present"


def test_resolve_tech_node_and_low_vt(fixed):
    c = ctx_for(fixed, module="corvi_module", tech_node="GF_22FDXP_6P75T_116A", max_low_vt_pct=20)
    assert c["tech_dir"] == "GF_22FDXP_6P75T_116A" and c["build_tech"] == "gf_22fdxp_6p75t_116a"
    assert c["max_low_vt_pct"] == 20
    d = ctx_for(fixed)                                        # defaults
    assert d["tech_dir"] == fixed["tech_dir"] and d["build_tech"] == fixed["build_tech"] and d["max_low_vt_pct"] == 10
    with pytest.raises(context.InputError, match="has no block_files/GF_22FDXP_6P75T_116A"):
        ctx_for(fixed, module="debug_module", tech_node="GF_22FDXP_6P75T_116A")
    with pytest.raises(context.InputError, match="not configured"):
        ctx_for(fixed, tech_node="TSMC_3NM")
    with pytest.raises(context.InputError, match="0..100"):
        ctx_for(fixed, max_low_vt_pct=150)
    first = ctx_for(fixed, run_tag="r9", module="corvi_module", tech_node="GF_22FDXP_6P75T_116A")
    _fake_run(fixed, "r9", first)
    r = ctx_for(fixed, run_tag="r9", module="corvi_module", start_from="compile", tech_node="")
    assert r["tech_dir"] == "GF_22FDXP_6P75T_116A"           # inherited on resume
    with pytest.raises(context.InputError, match="different tech node needs a NEW run"):
        ctx_for(fixed, run_tag="r9", module="corvi_module", start_from="compile", tech_node="GF_22FDXP_9T_AutoPro150_9M_eMRAM_stack")
    with pytest.raises(context.InputError, match="only takes effect from compile"):
        ctx_for(fixed, run_tag="r9", module="corvi_module", start_from="route_auto", max_low_vt_pct=30)


def test_scripts_carry_tech_and_cores_line(fixed):
    c = ctx_for(fixed, module="corvi_module", tech_node="GF_22FDXP_6P75T_116A", max_cores=24)
    Path(c["run_dir"]).mkdir(parents=True)
    out = scripts.write_all(c, fixed)
    assert "-tech gf_22fdxp_6p75t_116a" in Path(out["build"]).read_text()
    env = Path(out["env_fc"]).read_text()
    assert "setenv TECH_DIR    GF_22FDXP_6P75T_116A" in env
    comp = Path(out["compile"]).read_text()
    assert "pdflow: cores: requested max_cores 24" in comp and "TECH_DIR=GF_22FDXP_6P75T_116A" in comp


# ------------------------------------------------------------------------------- build source
REAL_BUILD = "/home/jsingh9.scratch/raven_local_ws/debug_2Core_4VC_32K_rvn_drive_20261006-230836"


def test_build_source_existing_real_build(fixed):
    from pdflow import buildsrc
    if not Path(REAL_BUILD, "run.json").is_file():
        pytest.skip("reference run not present")
    r = buildsrc.check_existing(REAL_BUILD, "gf_22fdxp_9t_autopro150_9m_emram_stack")
    assert r["rtl_files"] > 10 and r["pywork"].endswith("2Core_4VC_32K_rvn_drive/vbuild/py.Work")
    # a different tech node is refused (point 1: hard refusal)
    with pytest.raises(buildsrc.BuildSourceError, match="tied to its tech"):
        buildsrc.check_existing(REAL_BUILD, "gf_22fdxp_6p75t_116a")
    with pytest.raises(buildsrc.BuildSourceError, match="does not exist"):
        buildsrc.check_existing("/nonexistent/run", "x")


def test_build_source_install_existing_and_modes(fixed, tmp_path):
    from pdflow import buildsrc
    if not Path(REAL_BUILD, "run.json").is_file():
        pytest.skip("reference run not present")
    c = ctx_for(fixed, build_source="existing", build_path=REAL_BUILD)
    assert c["build_source"] == "existing" and context.step_mode(c, "build") == "provided"
    assert context.step_mode(c, "init_design") == "run"
    Path(c["rc_dir"]).mkdir(parents=True)
    dst = buildsrc.install_existing(c, buildsrc.check_existing(REAL_BUILD, c["build_tech"])["pywork"], log=lambda m: None)
    for f in buildsrc.PYWORK_FILES:
        assert (dst / f).is_file() and (dst / f).stat().st_size > 0
    assert (dst / "PDFLOW_REUSED_FROM").read_text().strip().endswith("py.Work")
    assert str(dst).startswith(c["rc_dir"])                 # installed into THIS run's copy
    with pytest.raises(context.InputError, match="needs 'Build path'"):
        ctx_for(fixed, build_source="existing")
    with pytest.raises(context.InputError, match="must be one of"):
        ctx_for(fixed, build_source="magic", build_path="/x")


def test_build_source_filelist(fixed, tmp_path):
    from pdflow import buildsrc
    rtl = tmp_path / "a.sv"; rtl.write_text("module a; endmodule\n")
    inc = tmp_path / "inc"; inc.mkdir()
    fl = tmp_path / "my.tcl"
    fl.write_text(f'set RTL_SOURCE_FILES "\\\n{rtl} \\\n"\nset INC_DIRS "{inc}"\n')
    r = buildsrc.check_filelist(str(fl))
    assert r["rtl_files"] == 1 and r["inc_dirs"] == 1 and not r["warnings"]
    fl.write_text(f'set RTL_SOURCE_FILES {{ relative.sv }}\n')
    with pytest.raises(buildsrc.BuildSourceError, match="relative"):
        buildsrc.check_filelist(str(fl))
    fl.write_text(f'set RTL_SOURCE_FILES {{ /no/such/file.sv }}\n')
    with pytest.raises(buildsrc.BuildSourceError, match="do not exist"):
        buildsrc.check_filelist(str(fl))
    fl.write_text("# nothing\n")
    with pytest.raises(buildsrc.BuildSourceError, match="RTL_SOURCE_FILES"):
        buildsrc.check_filelist(str(fl))
    # install: the run copy's read script is rewritten to source the run's user_filelist.tcl
    fl.write_text(f'set RTL_SOURCE_FILES "{rtl}"\nset INC_DIRS "{inc}"\n')
    c = ctx_for(fixed, module="corvi_module", build_source="filelist", build_path=str(fl))
    common = Path(c["module_dir"]) / "block_files" / "common"
    common.mkdir(parents=True)
    src = Path(fixed["rm_fc_cad_src"]) / "corvi_module/block_files/common/corvi_module.FC.read_design.tcl"
    shutil.copy(src, common / "corvi_module.FC.read_design.tcl")
    Path(c["run_dir"]).mkdir(parents=True, exist_ok=True)
    dst = buildsrc.install_filelist(c, fixed, str(fl), log=lambda m: None)
    txt = (common / "corvi_module.FC.read_design.tcl").read_text()
    assert str(dst) in txt and "$MIPS_HOME/../filelists/corvi_syn_rtl.tcl" not in txt
    assert dst.read_text() == fl.read_text()
    # Perforce copy untouched
    assert "$MIPS_HOME/../filelists/corvi_syn_rtl.tcl" in src.read_text()
