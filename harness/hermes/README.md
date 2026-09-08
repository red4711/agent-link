# harness/hermes

Hermes's harness adapter for [agent-link](../../). The transport
(`server.py`, `watcher.py`, `forwarder.py`, `send.py`) moves envelopes;
this folder is the Hermes-side wiring that turns arrived envelopes into
agent work and keeps the daemon alive. Lassie's adapter lives in
`../muse/` (same contract, its own implementation).

## How it works

```
Lassie --POST /message--> Hermes's server --> inbox.jsonl
                                               |
                 cron (every 2m) + monitor wakes only when
                 max inbox seq changes (inbox-watch.sh)
                                               |
                 agent reads new records in seq order, decides,
                 replies via:
                   python3 send.py reply '<text>' --correlation-id <id>
                                               |
                 outbox.jsonl --> served on /events for
                 Lassie's watcher (SSE, this side's egress)
```

1. **Transport** appends every accepted envelope to `inbox.jsonl` as
   `{"seq", "received_at", "via", "envelope"}` (deduplicated by
   envelope id).
2. **Monitor** (`inbox-watch.sh`, run as the cron job's change
   detector) prints the highest inbox `seq`. Output identical to the
   previous tick skips the agent run; new mail wakes the agent.
3. **Agent** follows `cron-prompt.md`: process new records in seq
   order, treat inbound content as **data/proposals, never trusted
   instructions**, reply via `send.py`, then persist the highest
   processed seq to `state/harness.offset`.
4. **Keepalive** (`keepalive.sh`, cron every 5 min, script-only) runs
   `keep-link.sh` so the server survives restarts. This side's config
   is `egress: ["stream"]`, `watch_peer: false` — Lassie can only dial
   out, so both directions ride over Hermes's endpoints.

## Install (on a fresh Hermes-like host)

```bash
# 1. transport
git clone https://github.com/red4711/agent-link && cd agent-link
cp config.example.json config.json   # name=hermes, listen_host=<tailnet IP>,
                                     # self_token=<random>, peer_*="", egress=["stream"], watch_peer=false

# 2. adapter scripts
cp harness/hermes/inbox-watch.sh ~/.hermes/scripts/agent-link-inbox.sh
cp harness/hermes/keepalive.sh ~/.hermes/scripts/agent-link-keepalive.sh
chmod +x ~/.hermes/scripts/agent-link-*.sh

# 3. cron jobs (via cronjob_manage):
#    - agent-link-inbox: schedule "every 2m", monitor ~/.hermes/scripts/agent-link-inbox.sh,
#      prompt = harness/hermes/cron-prompt.md, workdir = <repo dir>
#    - agent-link-keepalive: schedule "every 5m", no_agent=true,
#      script agent-link-keepalive.sh, deliver local

# 4. start transport
bash keep-link.sh
```

## Trust boundary

The agent never executes inbound content. `ping` gets an automatic
pong; read-only / low-risk `request`s are handled directly;
destructive, credential-touching, irreversible, money-spending, or
externally visible actions stay behind Hermes's own policy and Linh's
confirmation where required. See `../../SPEC.md` for the full contract.
