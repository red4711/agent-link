# Keepalive (scheduler reference)

Lassie runs on a VM that can be restarted, so a periodic task makes sure the
agent-link daemons are alive. Any scheduler works (cron, systemd timer, the
Hatch scheduler); the requirement is just: **every few minutes, run
`keep-link.sh` and report only on change or error.**

## What it does

`bash ~/workspace/agent-link/keep-link.sh` (no arguments):

- Starts `server.py`, `watcher.py`, `forwarder.py` only if not already running
  (pidfiles in the install dir).
- Starts nothing until `config.json` has tokens/peer configured — quiet by
  design before first setup. Once tokens are configured, the next run picks
  them up automatically.

## Reference: Hatch scheduler job

On Lassie's host this is a Hatch scheduled task firing every 5 minutes with
this body:

```markdown
Ensure the agent-link daemons are alive: run `bash ~/workspace/agent-link/keep-link.sh` (it takes no arguments). It starts server/watcher/forwarder only if not running, and only when ~/workspace/agent-link/config.json has tokens/peer configured — until then it stays quiet by design. Stay silent when everything is already running; report only if a component had to be (re)started or if the script errors. After Linh configures real tokens/peer URL in config.json, the next run picks them up automatically.
```

Plain-cron equivalent:

```cron
*/5 * * * * bash $HOME/workspace/agent-link/keep-link.sh >/dev/null 2>&1
```
