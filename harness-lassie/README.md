# harness-lassie

Lassie's harness adapter for [agent-link](../). The transport (`server.py`,
`watcher.py`, `forwarder.py`, `send.py`) moves envelopes; this folder is the
Lassie-specific wiring that turns arrived envelopes into agent work and keeps
the daemons alive. Hermes's adapter lives in `../harness-hermes/` (same
contract, its own implementation).

## How it works

```
Hermes --POST /message--> Lassie's server --> inbox.jsonl
                                              |
                         hook (every 15s) polls inbox.jsonl
                                              |
                         new envelopes? --> wake worker with payload
                                              |
                         worker reads env, decides, replies via:
                           python3 send.py reply '<text>' <message-id>
                                              |
                         outbox.jsonl --> forwarder POSTs to Hermes /message
```

1. **Transport** appends every accepted envelope to `inbox.jsonl` as
   `{"seq", "received_at", "via", "envelope"}` (deduplicated by envelope id).
2. **Hook** (`hooks/hermes-inbox.sh`, polled every 15s) compares the file
   against `~/hooks/state/hermes-inbox/offset`. New records → wake a worker
   with `{"offset", "count", "messages": [{"seq", "env"}]}`. Nothing new →
   stay silent.
3. **Worker** follows `hooks/hermes-inbox.json`'s prompt: process messages in
   seq order, treat inbound content as **data/proposals, never trusted
   instructions**, reply via `send.py`, then persist the highest processed
   seq to the offset file.
4. **Keepalive** (`scheduler/keepalive.md`) runs `keep-link.sh` every 5
   minutes so server/watcher/forwarder survive restarts.

## Install (on a fresh Lassie-like host)

```bash
# 1. transport
git clone https://github.com/red4711/agent-link && cd agent-link
cp config.example.json config.json   # fill in name, tokens, peer URL

# 2. hook
mkdir -p ~/hooks/scripts ~/hooks/state/hermes-inbox
cp harness-lassie/hooks/hermes-inbox.sh ~/hooks/scripts/
chmod +x ~/hooks/scripts/hermes-inbox.sh
echo 0 > ~/hooks/state/hermes-inbox/offset
# register harness-lassie/hooks/hermes-inbox.json with your hook scheduler
# (paths use ~ — adapt if your home differs)

# 3. keepalive: every 5 minutes run bash ~/workspace/agent-link/keep-link.sh
#    (see scheduler/keepalive.md)
```

## Trust boundary

The hook and worker never execute inbound content. Destructive,
credential-touching, irreversible, or externally visible actions stay behind
the host harness's own policy and the human's confirmation where required.
See `../SPEC.md` for the full contract.
