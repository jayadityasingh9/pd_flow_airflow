"""Decide whether a step really succeeded.

Neither tool's exit status can be trusted on its own:
  * build.py exits 0 when MIPS_HOME is unset, and ignores filelist/peakrdl failures;
  * the FC RM scripts set sh_continue_on_error, so a step with Tcl errors still reaches its
    last line, writes its marker and exits 0 -- while hard `exit`s (no clocks, S19) print the
    normal "Thank you for using Fusion Compiler" too.
So each verdict combines exit code + marker freshness + log scan + expected outputs + a read
back of the values we patched.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import QOR_STEPS
from .qor import parse_report_qor

BUILD_RC_HINT = {1: "VCS/yaml/traceback", 2: "invalid config or arguments", 3: "filelist/tech/PRPL step",
                 4: "setup step", 5: "Config.pm generation", 94: "build.py missing", 95: "run copy missing"}
FC_RC_HINT = {95: "run copy missing", 96: "fc_shell not found after module load", 97: "env script unreadable",
              134: "fc_shell aborted (SIGABRT)",
              137: "fc_shell was KILLED (SIGKILL): out-of-memory killer or kill -9 -- pick a host with more free RAM",
              139: "fc_shell CRASHED (SIGSEGV, segmentation fault): check free RAM/swap on the host and the stack "
                   "limit (fc_limits); re-run on a quieter host",
              143: "fc_shell was terminated (SIGTERM): stopped by a user / Airflow"}


@dataclass
class Verdict:
    ok: bool = True
    fatal: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)

    def fail(self, msg):
        self.ok = False
        self.fatal.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def as_dict(self):
        return asdict(self)


def _mtime(p: Path) -> float | None:
    try:
        return p.stat().st_mtime
    except FileNotFoundError:
        return None


def _fresh(p: Path, ref: float | None, slack: float = 5.0) -> bool:
    m = _mtime(p)
    return m is not None and (ref is None or m >= ref - slack)


def _outcome_checks(v: Verdict, res: dict, hints: dict) -> None:
    if res["outcome"] == "timeout":
        v.fail(f"stopped by the flow after {res['elapsed_s'] / 3600:.1f} h: no new log output for the stall "
               f"timeout, or the max runtime was reached (config: stall_timeout_h / max_runtime_h) -- see the task log")
    elif res["outcome"] == "lost":
        v.fail("the job disappeared without writing an exit code (killed, crashed or host reboot)")
    elif res["rc"] != 0:
        v.fail(f"exit code {res['rc']}" + (f" ({hints[res['rc']]})" if res["rc"] in hints else ""))


# --- build -------------------------------------------------------------------------------------
def check_build(ctx: dict, res: dict) -> Verdict:
    v = Verdict()
    _outcome_checks(v, res, BUILD_RC_HINT)
    cfg = ctx["build_config"]
    wdir = Path(ctx["rc_dir"]) / "vbuild" / cfg
    ref = _mtime(Path(ctx["run_dir"]) / "status" / "build.start")

    blog = wdir / "build.log"
    if not _fresh(blog, ref):
        v.fail(f"{blog} missing or not from this run")
    else:
        text = blog.read_text(errors="replace")
        last = next((ln for ln in reversed(text.splitlines()) if ln.strip()), "")
        if "build - CRITICAL - Build Completed" not in last:
            v.fail(f"build.log does not end with 'Build Completed' (last line: {last.strip()[:200]!r})")
        for pat, what in ((r"Error-\[", "VCS Error-["), (r" - ERROR - ", "build.py ERROR"), (r"^Traceback", "Python traceback")):
            hits = re.findall(rf"^.*{pat}.*$", text, re.M)
            if hits:
                v.fail(f"{len(hits)} {what} line(s) in build.log, first: {hits[0].strip()[:200]}")
        v.facts["vcs_warnings"] = len(re.findall(r"Warning-\[", text))
    console = Path(ctx["run_dir"]) / "logs" / "build.console.log"
    if console.is_file() and re.search(r"^Traceback", console.read_text(errors="replace"), re.M):
        v.fail(f"Python traceback in {console}")

    if not (wdir / "vbuild" / "sim.Linux" / "sim").exists():
        v.fail("vbuild/sim.Linux/sim missing (VCS elaboration failed)")

    pywork = wdir / "vbuild" / "py.Work"
    top = Path(ctx["top_file"]).name
    for k in ("rtl", "inc", "pkg"):
        f = pywork / f"synthesis_{k}.f"
        if not _fresh(f, ref) or f.stat().st_size == 0:
            v.fail(f"{f.name} missing, empty or not from this run (no -t top? bomFilelist missing?)")
            continue
        if k == "rtl":
            text = f.read_text(errors="replace")
            if top not in text:
                v.fail(f"synthesis_rtl.f does not list the top file {top}")
            gen = set(re.findall(r"(/\S*/vbuild/\S*)", text))
            mine = str(wdir / "vbuild")
            foreign = [g for g in gen if not g.startswith(mine) and "/vbuild/" in g
                       and "/rm_fc_cad/vbuild/" in g]
            if foreign:
                v.fail(f"synthesis_rtl.f points at another run's build output, e.g. {sorted(foreign)[0]}")
            v.facts["rtl_files"] = text.count("\n")

    rdl = Path(ctx["rc_dir"]) / "vbuild" / "rdl_generate.log"
    if rdl.is_file() and _fresh(rdl, ref):
        rdl_text = rdl.read_text(errors="replace")
        bad = [x for x in re.findall(r"Return code:\s*(-?\d+|None)", rdl_text) if x == "None" or int(x) != 0]
        if bad:
            v.fail(f"peakrdl failed in rdl_generate.log ({len(bad)} non-zero return code(s))")
    else:
        v.warn("rdl_generate.log missing/stale (register generation not verified)")
    return v


# --- FC ------------------------------------------------------------------------------------------
ERR_ID = re.compile(r"^Error:.*\(([A-Z][A-Z0-9_]*-\d+)\)\s*$")
CLK_ECHO = re.compile(r"^\s*set\s+Clk_period\s+([0-9.]+)")
CORES_ECHO = re.compile(r"^\s*set_host_options\s+-max_cores\s+(\d+)")
THREADS_RE = re.compile(r"^Information: Timer using (\d+) threads")


MSG_ROW = re.compile(r"^([A-Z][A-Z0-9_]*-\d+[a-z]?)\s+Error\s+\d+\s+(\d+)\s+(\d+)\s*$")
FATAL_ID_FAMILIES = re.compile(r"^(ELAB|VER|LNK|NDM)-")


def scan_fc_log(path: Path, fatal_patterns: list[str]) -> dict:
    fatal_re = [re.compile(p) for p in fatal_patterns]
    out = {"fatal": [], "error_ids": Counter(), "error_first": {}, "rm_errors": [], "thank_you": False,
           "clk_echo": None, "cores_echo": None, "timer_threads": None, "lines": 0, "s19_continued": False,
           "error_source": "log lines"}
    table: dict = {}
    in_table = False
    with open(path, errors="replace") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            out["lines"] = n
            # `print_message_info -ids * -summary` (just before each step's marker): every message
            # id with occurrences + suppressed -- also errors from redirected commands / multi-line
            if line.startswith("print_message_info -ids * -summary"):
                in_table, table = True, {}
                continue
            if in_table:
                m = MSG_ROW.match(line)
                if m:
                    table[m.group(1)] = int(m.group(2)) + int(m.group(3))
                    continue
                if line.startswith(("Id ", "---", "=")) or not line.strip() or re.match(r"^[A-Z][A-Z0-9_]*-\d+", line):
                    continue
                in_table = False
            if "Thank you for using Fusion Compiler" in line:
                out["thank_you"] = True
            if line.startswith("pdflow: Rule S19 violation exists -- CONTINUING"):
                out["s19_continued"] = True
            m = CLK_ECHO.match(line)
            if m:
                out["clk_echo"] = m.group(1)
            m = CORES_ECHO.match(line)
            if m:
                out["cores_echo"] = int(m.group(1))
            m = THREADS_RE.match(line)
            if m:
                out["timer_threads"] = int(m.group(1))
            if line.startswith("RM-error") and len(out["rm_errors"]) < 50:
                out["rm_errors"].append(f"{n}: {line[:300]}")
            if line.startswith("Error") or line.startswith("Fatal") or "RM-error" in line or "exiting" in line \
                    or "Exiting" in line or "fault" in line or "core dumped" in line or "icense" in line:
                for r in fatal_re:
                    if r.search(line):
                        out["fatal"].append(f"line {n}: {line[:300]}")
                        break
                m = ERR_ID.match(line)
                if m:
                    out["error_ids"][m.group(1)] += 1
                    out["error_first"].setdefault(m.group(1), f"line {n}: {line[:300]}")
    if table:                                  # the tool's own count wins over our line scan
        out["error_ids"] = Counter(table)
        out["error_source"] = "message summary table"
    for eid in out["error_ids"]:
        if FATAL_ID_FAMILIES.match(eid):
            out["fatal"].append(f"{eid} x{out['error_ids'][eid]}: "
                                + out["error_first"].get(eid, "(see the message summary table)"))
    return out


def _clk_matches(requested_ns: float, reported: float) -> bool:
    for scale in (1.0, 1000.0):                 # library time unit ns or ps
        if abs(reported - requested_ns * scale) <= max(1e-3 * scale, 0.005 * requested_ns * scale):
            return True
    return False


def check_fc_step(ctx: dict, fixed: dict, step: str, res: dict, allow_ids: set | None = None) -> Verdict:
    v = Verdict()
    _outcome_checks(v, res, FC_RC_HINT)
    tmp = Path(ctx["module_dir"]) / "rm_fc" / "tmp"
    rpts = Path(ctx["module_dir"]) / "rm_fc" / "rpts" / step
    log = Path(ctx["module_dir"]) / "rm_fc" / "logs" / f"{step}.log"
    ref = _mtime(Path(ctx["run_dir"]) / "status" / f"{step}.start")

    # F2 marker: written by the LAST lines of <step>.tcl -> proves the script ran to the end
    if not _fresh(tmp / step, ref):
        v.fail(f"marker {tmp / step} missing/stale: the script stopped before its end "
               f"(hard exit such as 'No clocks created' or S19, crash, license)")

    if not log.is_file():
        v.fail(f"{log} missing")
        return v
    scan = scan_fc_log(log, fixed["fatal_patterns"])
    v.facts.update({"log_lines": scan["lines"], "clk_echo": scan["clk_echo"], "cores_echo": scan["cores_echo"],
                    "timer_threads": scan["timer_threads"],
                    "error_ids": dict(scan["error_ids"])})
    # F3
    if not scan["thank_you"]:
        v.fail("no 'Thank you for using Fusion Compiler' in the log: fc_shell crashed or was killed")
    # F4
    for line in list(dict.fromkeys(scan["fatal"]))[:10]:
        v.fail(f"fatal: {line}")
    # F5 the patched values really reached FC (both files are sourced with -echo)
    if scan["clk_echo"] is None:
        v.warn("could not find 'set Clk_period' echoed in the log (patch not confirmed)")
    elif abs(float(scan["clk_echo"]) - float(ctx["clk_str"])) > 1e-9:
        v.fail(f"FC used Clk_period {scan['clk_echo']} but {ctx['clk_str']} was requested (patch had no effect)")
    if scan["cores_echo"] is None:
        v.warn("could not find 'set_host_options -max_cores' echoed in the log")
    elif scan["cores_echo"] != ctx["max_cores"]:
        v.fail(f"FC used -max_cores {scan['cores_echo']} but {ctx['max_cores']} was requested")

    # F6 expected outputs
    if step == "init_design":
        if not (tmp / f"{ctx['design_name']}.nlib").is_dir():
            v.fail(f"design library {ctx['design_name']}.nlib not created")
    if step in QOR_STEPS:
        for suf in ("report_qor", "report_global_timing"):
            f = rpts / f"{step}.{suf}"
            if not _fresh(f, ref) or f.stat().st_size == 0:
                v.fail(f"{f} missing/empty/stale")
        if not (rpts / f"{step}.qor.qor").is_file():
            v.warn(f"{step}.qor.qor not written")
        # F7 clock actually constrained as requested
        qf = rpts / f"{step}.report_qor"
        if qf.is_file():
            groups = parse_report_qor(qf.read_text(errors="replace"))["groups"]
            clk = [g for g in groups if g["group"] == "Clk" and g["clk_period"] is not None]
            if not clk:
                v.fail("report_qor has no path group 'Clk' with a clock period: the design is not "
                       "constrained as expected (SDC not read / clock port not found?)")
            else:
                rep = clk[0]["clk_period"]
                v.facts["report_clk_period"] = rep
                if not _clk_matches(float(ctx["clk_str"]), rep):
                    v.fail(f"report_qor 'Clk' period is {rep}, requested {ctx['clk_str']} ns")

    # routing: open nets after route_opt = incomplete route -> FAIL; DRCs -> WARN (+ optional limit)
    if step in ("route_auto", "route_opt"):
        cr = rpts / f"{step}.check_routes"
        if cr.is_file():
            t = cr.read_text(errors="replace")
            mo = re.search(r"Total number of open nets\s*=\s*(\d+)", t)
            md = re.search(r"Total number of DRCs\s*=\s*(\d+)", t)
            if mo:
                v.facts["open_nets"] = int(mo.group(1))
                if int(mo.group(1)) > 0:
                    (v.fail if step == "route_opt" else v.warn)(f"{mo.group(1)} open net(s) after {step}")
            if md:
                v.facts["drcs"] = int(md.group(1))
                lim = fixed.get("max_route_opt_drcs")
                if step == "route_opt" and lim is not None and int(md.group(1)) > int(lim):
                    v.fail(f"{md.group(1)} DRCs after route_opt (limit {lim})")
        else:
            v.warn(f"{cr.name} not found: routing completeness not checked")

    # F8 other errors: listed, and WARN (or FAIL per unknown_error_policy)
    allow = allow_ids or set()
    unknown = {k: c for k, c in scan["error_ids"].items() if k not in allow}
    if unknown:
        msg = "Error IDs in log: " + ", ".join(f"{k} x{c}" for k, c in sorted(unknown.items()))
        (v.fail if fixed.get("unknown_error_policy") == "fail" else v.warn)(msg)
        for k in sorted(unknown)[:5]:
            if k in scan["error_first"]:
                v.warn(f"  first {k}: {scan['error_first'][k]}")
    if scan["s19_continued"]:
        n = "?"
        for f in sorted(rpts.glob(f"{step}.initial_opto.*.dft_drc")):
            m = re.search(r"Rule S19 \([^)]*\) was violated (\d+) times", f.read_text(errors="replace"))
            if m:
                n = m.group(1)
        v.facts["dft_s19_violations"] = n
        v.warn(f"DFT Rule S19 violated ({n}x) -- continued because continue_past_s19 was ON "
               f"(details: {rpts / 'dft_drc_full.rpt'})")
    if scan["rm_errors"]:
        v.warn(f"{len(scan['rm_errors'])} RM-error line(s), first: {scan['rm_errors'][0]}")
    return v
