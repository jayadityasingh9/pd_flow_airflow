# 4. Open the UI from your PC

The Airflow web UI listens **only on the loopback interface of the host it runs on** (`127.0.0.1:<port>`).
Nothing on the network can reach it directly; your browser gets there through an **ssh tunnel** that you open
from your PC. This is deliberate: the UI can launch jobs under your account.

Two facts you need:

- **The host** your Airflow runs on (mipscs85 unless you started it elsewhere).
- **Your port**: printed by `start.sh`, or `grep AIRFLOW_PORT ~/.pdflow/local.env`. It is `20000 + (your uid % 20000)`.

## Windows, PowerShell (recommended)

1. Open **PowerShell** on your PC (Windows key, type `PowerShell`, Enter). The prompt looks like `PS C:\Users\...>`.
   If it looks like `[you@mipscs85 ~]$` you are already inside Linux: open another window.
2. Type (replace `<port>` and `<you>`):
   ```powershell
   ssh -L <port>:127.0.0.1:<port> <you>@mipscs85.mipstec.com
   ```
   First time: answer `yes` to the host-key question. Enter your password unless you did page 2 B.
   You land in a normal Linux shell. **This window is the tunnel**: leave it open (minimise it).
3. Browser on Windows: `http://localhost:<port>`
4. Log in: user `admin`, password from Linux:
   ```csh
   cat ~/.pdflow/home/simple_auth_manager_passwords.json.generated
   ```
   (clone users: `<clone>/airflow/home/simple_auth_manager_passwords.json.generated`)
5. Done for the day: `exit` in the tunnel window. Airflow keeps running on the host.

Variant without a shell: `ssh -N -L <port>:127.0.0.1:<port> <you>@mipscs85.mipstec.com` just sits there silently.
Ctrl-C closes it.

## Windows, PuTTY

Session → Host Name `mipscs85.mipstec.com`. Connection → SSH → Tunnels: Source port `<port>`, Destination
`127.0.0.1:<port>`, **Add**. Back to Session, Save, Open, log in. While that window is open,
`http://localhost:<port>` works in your Windows browser. MobaXterm: Tools → MobaSSHTunnel, local port `<port>`,
remote server `127.0.0.1` port `<port>`, via `mipscs85` with your user.

## Linux desktop or VNC on a gateway (mipsvgw8x)

```csh
ssh -f -N -L <port>:127.0.0.1:<port> mipscs85      # once per login; -f puts it in the background
firefox http://127.0.0.1:<port> &
```

Check whether a tunnel already exists before opening another: `pgrep -af 'ssh .*-L <port>'`. Close it with
`pkill -f 'L <port>:127.0.0.1:<port>'`.

## Common mistakes

| What you see | Why | Do |
|---|---|---|
| `bind: Address already in use … Could not request local forwarding` | a tunnel for that port is already open (an earlier window, or `-f` from before) | nothing: just open the browser. One tunnel per port is enough |
| Browser: "This site can't be reached / connection refused" on `localhost:<port>` | the tunnel window was closed, or the command was typed into a Linux window instead of PowerShell | open the tunnel again from **Windows** |
| Same, tunnel is open | Airflow is not running on the host | `bash .../airflow/status.sh` on the host; start it (page 3) |
| `Bad : modifier in $ 'U'` / `Illegal variable name` | a PowerShell command typed into a Linux (csh) window | run it in PowerShell |
| `ssh: The term 'ssh' is not recognized` | your Windows has no OpenSSH client | use PuTTY (above) or install "OpenSSH Client" from Windows optional features |
| `Connection timed out` to mipscs85 | your PC cannot reach that host directly | tell the owner; `airflow/ui-tunnel.sh` can relay through a login gateway |
| Login page rejects `admin` | wrong file: each instance has its own password | re-read the file from step 4 on the host that runs **your** Airflow |
| UI loads but says "DAG is paused" / no Trigger button | new DB came up paused or stale | `bash .../airflow/af.sh dags unpause raven_pd_flow`; `doctor` reports it |

## Where in the UI

- **DAGs → raven_pd_flow → Trigger** (top right): the form (page 5).
- **Graph**: the 13 boxes; click a box → **Logs** for the live FC log tail, **Required Actions** on a gate.
- **Browse → Required Actions**: all gates waiting for you.
- Clear a task: box → **Clear task** (re-attaches to a still-running job; re-runs a finished one).
- Stop a job: box → **Mark as failed** (kills the remote FC job). Airflow-internal stops never kill jobs.
