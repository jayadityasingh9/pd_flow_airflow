# 1. Prerequisites

Everything below is checked by one command once you have run the setup (page 3):

```csh
bash /home/jsingh9.scratch/mywork/pd_flow_airflow/bin/pdflow.sh doctor
```

Every FAIL line tells you how to fix it. Before Airflow is started, two or three WARN lines ("not running",
"DAG not registered yet", "log-server ports") are normal.

## Your account

| Need | Why | Check |
|---|---|---|
| A MIPS Linux account | everything runs as you | `id` |
| A scratch area `/home/<you>.scratch` with free space | runs go there: ≥ 10 GB for debug/trf, 50 GB for cic, **150 GB for corvi** (a corvi run dir is 5–8 GB; precheck refuses when space is short) | `df -h /home/$USER.scratch` |
| `module load fc/2022.12-SP6` works for you on the pd hosts | every FC step uses it (and the Synopsys license it points at) | `doctor` probes it |
| Read access to a Raven Perforce workspace | the flow copies `rm_fc_cad` from it and FC reads the RTL from it | default is the owner's `/home/jsingh9/Perforce/jsingh9_raven_ws2` (world-readable); use your own via `mips_home` in `~/.pdflow/local.yaml` |
| **Passwordless ssh** from the Airflow host to `mipspd81 … mipspd85` under your account | every build/FC step is started there over ssh with `BatchMode=yes` (no prompt possible) | page 2 |

## Hosts

**Where Airflow runs (yours):** a host with `/usr/bin/python3.11` and `tmux`. Today that is **mipscs85**
(64 cores, 1 TB RAM; recommended), `mipspd86`, `mipspd81`, `mipscs82`, `mipscs83`. It is **not** on
mipspd82–85. Airflow itself is light: about 2 GB RAM and 2 % of one core when idle, running at low priority.
Several people can run their own instance on the same host; each gets its own port.

**Where the tools run (chosen per run in the form):** the pd hosts.

| Host | Cores | Notes |
|---|---|---|
| mipspd81 | 128 | often busy |
| mipspd82 | 128 | often busy |
| mipspd83 | 32 | |
| mipspd84 | 32 | usually the least loaded |
| mipspd85 | 32 | |

Precheck prints the live load and free RAM of all five when you trigger, and refuses a host with less free RAM
than the module needs (debug/trf 16 GB, cic 64 GB, corvi **200 GB**). It suggests the best host in the error.

## Your PC

- Windows: PowerShell (Windows 10/11 has the `ssh` command built in) **or** PuTTY/MobaXterm. Only used for the
  ssh tunnel to the Airflow host; the browser is yours.
- Linux desktop / VNC: a terminal and Firefox.

## What you do NOT need

- Root, sudo, IT tickets.
- Internet access (with the shared install; a private clone's `setup.sh` does need PyPI).
- A GitHub account. The code is on NFS.
