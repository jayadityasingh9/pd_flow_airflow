# 2. Passwordless ssh

Two different hops, two different keys:

| Hop | Required? | Key lives on | Why |
|---|---|---|---|
| **A. Linux → pd hosts** (Airflow host → mipspd81…85) | **yes** | your Linux home (`~/.ssh`, on NFS, same on every host) | Airflow launches every step with `ssh -o BatchMode=yes`; a password prompt = the step fails |
| **B. Windows → Linux** (your PC → mipscs85) | no, convenient | your Windows PC | without it the tunnel asks for your password once per session |

Your home directory is one NFS export mounted on every MIPS host, so `~/.ssh/authorized_keys` is **one file**
seen by all of them. A key added once works for every host.

---

## A. Linux → pd hosts (required)

Do this once, on any Linux host (e.g. mipscs85), as yourself. Commands are csh.

### A.1 Make a key, if you do not have one

```csh
ls ~/.ssh/id_ed25519.pub
```

If that prints the file, skip to A.2. Otherwise:

```csh
ssh-keygen -t ed25519
```

Press **Enter** at all three prompts (default file, **empty passphrase** twice). The passphrase must be empty:
Airflow runs from cron/tmux with no agent to unlock a key.

### A.2 Authorise the key for your own account

```csh
mkdir -p ~/.ssh
chmod 700 ~/.ssh
cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
chmod go-w ~
```

The permissions matter: sshd ignores `authorized_keys` if `~/.ssh` is not 700, the file is not 600, or your
home directory is writable by group/others.

### A.3 Accept each pd host's key once

The first connection to a host asks `Are you sure you want to continue connecting?`. That prompt also breaks
BatchMode, so answer it once for every pd host now:

```csh
foreach h (mipspd81 mipspd82 mipspd83 mipspd84 mipspd85)
  ssh -o StrictHostKeyChecking=accept-new $h hostname
end
```

Each line must print the host name and nothing else. Use the **short names** exactly as above; `mipspd84` and
`mipspd84.mipstec.com` count as different hosts for this prompt and the tool uses the short names.

### A.4 Prove it

```csh
foreach h (mipspd81 mipspd82 mipspd83 mipspd84 mipspd85)
  ssh -o BatchMode=yes $h hostname
end
```

Five host names, no prompts, no errors. `pdflow.sh doctor` runs the same test (rows `ssh mipspd8x`).

### A.5 If it still asks for a password

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied (publickey,...)` with BatchMode | key not in `authorized_keys`, or permissions | redo A.2; `ls -la ~ ~/.ssh` and compare with the modes above |
| `Host key verification failed` | host key not accepted yet | A.3 for that host |
| works from one host, not another | it cannot: the home is shared. Check you are the same user (`id -un`) on both | |
| `Enter passphrase for key` | the key has a passphrase | make a new key with an empty passphrase (A.1) and add it (A.2) |
| ssh is slow (10 s) but works | GSSAPI/DNS probing | harmless; add `GSSAPIAuthentication no` to `~/.ssh/config` if it bothers you |

---

## B. Windows → Linux (convenient)

Without this, every tunnel you open from Windows asks for your password once. With it, the tunnel is silent.

### B.1 PowerShell (Windows 10/11 built-in ssh)

On your PC, open PowerShell (not a Linux window: the prompt must be `PS C:\...>`).

```powershell
# 1. make a key if you have none (Enter at every prompt)
ssh-keygen -t ed25519

# 2. show the public key
type $env:USERPROFILE\.ssh\id_ed25519.pub
```

Copy the whole line (`ssh-ed25519 AAAA… name@PC`). Then either

**(a)** append it from Windows in one go (asks your password this one time):
```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh <you>@mipscs85.mipstec.com "cat >> ~/.ssh/authorized_keys"
```

**(b)** or paste it on Linux yourself:
```csh
echo 'ssh-ed25519 AAAA...paste the whole line...' >> ~/.ssh/authorized_keys
```

Test from PowerShell: `ssh <you>@mipscs85.mipstec.com hostname` prints `mipscs85` with no password (answer
`yes` once to the host-key question).

Optional, makes the tunnel a one-word command. Create the text file `C:\Users\<you>\.ssh\config` (no extension):

```
Host af
    HostName mipscs85.mipstec.com
    User <you>
    LocalForward <port> 127.0.0.1:<port>
    ServerAliveInterval 60
```

`<port>` is the one `start.sh` printed for you (page 3). Then `ssh -N af` opens the tunnel.

### B.2 PuTTY

1. **PuTTYgen** → Generate (EdDSA/Ed25519) → leave the passphrase empty → Save private key (`.ppk`).
2. Copy the text in the box "Public key for pasting into OpenSSH authorized_keys file" and run on Linux:
   ```csh
   echo 'ssh-ed25519 AAAA...pasted...' >> ~/.ssh/authorized_keys
   ```
3. PuTTY session for `mipscs85.mipstec.com`: Connection → SSH → Auth → Credentials → Private key file = the `.ppk`.
   Connection → Data → Auto-login username = `<you>`. Save the session.

### B.3 MobaXterm

Session → SSH → Advanced SSH settings → "Use private key" with a key made by its built-in generator (or the `.ppk`
from PuTTYgen); add its public text to `~/.ssh/authorized_keys` as in B.2 step 2.

---

## What NOT to do

- Do not copy your **private** key (`id_ed25519`, `.ppk`) to shared places or into the project directory.
- Do not run the PowerShell commands (`$env:...`, `type ...`) in a Linux window; csh prints
  `Bad : modifier in $` or `Illegal variable name`. PowerShell commands go in PowerShell, csh commands go in Linux.
- Do not put a passphrase on the Linux key used by Airflow.
