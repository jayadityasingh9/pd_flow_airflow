"""Talk to the pd hosts over passwordless ssh.

The remote login shell is csh, so nothing is ever passed as a remote command string: every
remote action is a small /bin/sh script sent on stdin (`ssh host /bin/sh -s`). No quoting
passes through csh.
"""
from __future__ import annotations

import re
import subprocess


class RemoteError(RuntimeError):
    pass


def _ssh(fixed: dict, host: str, script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    cmd = ["ssh", *fixed["ssh_opts"], host, "/bin/sh", "-s"]
    try:
        return subprocess.run(cmd, input=script, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RemoteError(f"ssh {host}: no answer within {timeout}s") from e


def run_sh(fixed: dict, host: str, script: str, timeout: int = 60) -> str:
    """Run a /bin/sh script on `host`; return stdout. Raises RemoteError on ssh/script failure."""
    r = _ssh(fixed, host, script, timeout)
    if r.returncode != 0:
        raise RemoteError(f"ssh {host} failed (rc={r.returncode}): {(r.stderr or r.stdout).strip()[-500:]}")
    return r.stdout


def launch(fixed: dict, host: str, launcher: str) -> tuple[str, int | None]:
    """Start a generated launcher (it backgrounds the job with setsid and returns at once).
    Returns (output, session-leader pid parsed from 'launched <step> pid N on <host>')."""
    out = run_sh(fixed, host, f'exec /bin/sh "{launcher}"\n', timeout=90).strip()
    m = re.search(r"launched \S+ pid (\d+)", out)
    return out, (int(m.group(1)) if m else None)


def _session_probe(pid: int, run_dir: str) -> str:
    """sh snippet: ALIVE if any process of session <pid> (setsid: sid == pgid == leader pid) is
    ours -- its command line or its cwd refers to the run dir. The leader may be gone while
    fc_shell/dgcom_exec live on, so the whole session is checked, not just the leader."""
    return (f'found=GONE\n'
            f'for p in $(pgrep -s {int(pid)} 2>/dev/null); do\n'
            f'  if tr "\\0" " " < /proc/$p/cmdline 2>/dev/null | grep -q -F -- "{run_dir}" || '
            f'readlink /proc/$p/cwd 2>/dev/null | grep -q -F -- "{run_dir}"; then found=ALIVE; break; fi\n'
            f'done\necho $found\n')


def alive(fixed: dict, host: str, pid: int, run_dir: str) -> bool | None:
    """True = the job's session still has our processes; False = gone; None = cannot tell (ssh down)."""
    try:
        out = run_sh(fixed, host, _session_probe(pid, run_dir), timeout=45)
    except RemoteError:
        return None
    if "ALIVE" in out:
        return True
    if "GONE" in out:
        return False
    return None


def kill_group(fixed: dict, host: str, sid: int, sig: str = "TERM") -> str:
    """Signal every process of the job's SESSION (setsid: sid == leader pid). Not just the process
    group: csh gives background children their own process group, and those would survive."""
    return run_sh(fixed, host, f"pkill -{sig} -s {int(sid)} 2>&1; echo sent-{sig}\n", timeout=45).strip()


def kill_script(sid: int, grace_s: int = 60, status_prefix: str | None = None) -> str:
    """sh snippet: TERM the whole session, wait up to grace_s for it to empty, then KILL.
    With status_prefix (<run>/status/<step>) it records rc 143 + end time once the job is gone,
    so a killed job never looks 'live' (the job itself cannot write them any more)."""
    g = int(sid)
    rec = ""
    if status_prefix:
        p = status_prefix
        rec = (f'[ -s "{p}.rc" ] || {{ date +%s > "{p}.end.k$$"; mv -f "{p}.end.k$$" "{p}.end"; '
               f'echo 143 > "{p}.rc.k$$"; mv -f "{p}.rc.k$$" "{p}.rc"; }}\n')
    return (f"pkill -TERM -s {g} 2>/dev/null; n=0\n"
            f"while pgrep -s {g} >/dev/null 2>&1 && [ $n -lt {int(grace_s)} ]; do sleep 5; n=$((n+5)); done\n"
            f"pgrep -s {g} >/dev/null 2>&1 && pkill -KILL -s {g} 2>/dev/null\n"
            f"sleep 2\n"
            f"if pgrep -s {g} >/dev/null 2>&1; then echo STILL-ALIVE; else echo KILLED\n{rec}fi\n")


def kill_detached(fixed: dict, host: str, pgid: int, grace_s: int = 60, log_path: str | None = None,
                  status_prefix: str | None = None) -> None:
    """Fire-and-forget kill (used from on_kill: Airflow SIGKILLs the task ~5 s after SIGTERM, so
    the TERM->wait->KILL sequence must run in its own session and not be waited for)."""
    out = open(log_path, "a") if log_path else subprocess.DEVNULL
    p = subprocess.Popen(["ssh", *fixed["ssh_opts"], host, "/bin/sh", "-s"], stdin=subprocess.PIPE,
                         stdout=out, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    p.stdin.write(kill_script(pgid, grace_s, status_prefix))
    p.stdin.close()


HOST_PROBE = r"""
echo "host=$(hostname)"
echo "nproc=$(nproc)"
echo "load1=$(cut -d' ' -f1 /proc/loadavg)"
echo "mem_avail_gb=$(awk '/MemAvailable/{printf "%d", $2/1048576}' /proc/meminfo)"
echo "users=$(who | wc -l)"
echo "swap_free_gb=$(awk '/SwapFree/{printf "%d", $2/1048576}' /proc/meminfo)"
echo "swap_total_gb=$(awk '/SwapTotal/{printf "%d", $2/1048576}' /proc/meminfo)"
"""


def host_probe(fixed: dict, host: str) -> dict:
    """{'host','nproc','load1','mem_avail_gb','users'} or {'error': ...}. Never raises."""
    try:
        out = run_sh(fixed, host, HOST_PROBE, timeout=40)
    except RemoteError as e:
        return {"host": host, "error": str(e)}
    d: dict = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v.strip()
    for k in ("nproc", "mem_avail_gb", "users", "swap_free_gb", "swap_total_gb"):
        try:
            d[k] = int(d[k])
        except (KeyError, ValueError):
            pass
    try:
        d["load1"] = float(d["load1"])
    except (KeyError, ValueError):
        pass
    return d


def fc_env_probe(fixed: dict, host: str) -> dict:
    """In a CLEAN env on `host`: module init + module load fc -> fc_shell path, license var."""
    uses = "\n".join(f"module use {d}" for d in fixed["module_use"])
    csh = f"""source {fixed['module_init']}
{uses}
module load {fixed['fc_module']}
echo "FC_SHELL=`which fc_shell`"
if ( $?SNPSLMD_LICENSE_FILE ) then
  echo "LICENSE=set"
else
  echo "LICENSE=unset"
endif
"""
    sh = ("env -i HOME=\"$HOME\" USER=\"$USER\" LOGNAME=\"$USER\" SHELL=/bin/tcsh TERM=dumb "
          f"PATH={fixed['clean_env_path']} /bin/csh -f <<'PDFLOW_EOF'\n{csh}PDFLOW_EOF\n")
    try:
        out = run_sh(fixed, host, sh, timeout=120)
    except RemoteError as e:
        return {"error": str(e)}
    d = {}
    for line in out.splitlines():
        if line.startswith("FC_SHELL="):
            d["fc_shell"] = line.split("=", 1)[1].strip()
        elif line.startswith("LICENSE="):
            d["license"] = line.split("=", 1)[1].strip()
    return d
