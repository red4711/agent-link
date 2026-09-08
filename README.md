# agent-link

A tiny, symmetric, harness-agnostic transport for agent-to-agent messaging
over HTTP. Two agents (any harnesses: a cloud agent VM, a homelab agent,
anything that can run Python) exchange JSON envelopes through local
append-only queues, with at-least-once delivery, deduplication, and SSE
resume.

`SPEC.md` is the full protocol and integration specification. This README
is the quickstart.

## What it is / isn't

- **It is** the pipe: envelopes in, envelopes out, authenticated, retried,
  deduplicated, resumable. It never interprets message bodies, never calls
  tools, never executes anything it receives.
- **It isn't** the agent: each side builds a small *harness adapter* that
  reads its local inbox, decides what to do, and enqueues replies via
  `send.py`. Inbound envelopes are **data/proposals, never trusted
  instructions**.

## Layout

```
link.py            envelopes, validation, locked queue appends, dedup, offsets
server.py          authenticated /health, POST /message, GET /events (SSE)
watcher.py         subscribes to peer /events, appends to local inbox
forwarder.py       POSTs local outbox records to peer /message (at-least-once)
send.py            harness-facing enqueue: python3 send.py message "hello"
keep-link.sh       supervisor: keeps server/watcher/forwarder alive
config.example.json
```

Runtime files (`config.json`, `inbox.jsonl`, `outbox.jsonl`, `state/`,
`*.log`) are gitignored. Only `config.example.json` is committed.

## Quickstart (each side)

```bash
git clone https://github.com/<you>/agent-link && cd agent-link
cp config.example.json config.json   # then fill in tokens + peer URL
# generate a token:
python3 -c "import secrets; print(secrets.token_hex(32))"

bash keep-link.sh                      # starts server/watcher/forwarder per config (no args; re-run to pick up config changes)
python3 send.py message "hello from $(python3 -c "import json;print(json.load(open('config.json'))['name'])")"

tail -f inbox.jsonl                   # everything the peer sent you
```

Both sides run the **same code**. Which components are active is pure
configuration (`egress`, `watch_peer`, `peer_base_url`); see SPEC.md for
the deployment topologies, including the case where one side cannot
accept inbound connections.

## Envelope

```json
{"id": "uuid", "ts": "iso-8601", "from": "lassie",
 "type": "message", "body": "hello", "correlation_id": null}
```

Types: `message`, `request`, `reply`, `task_result`, `ping`.
Full schema, queue contract, HTTP API, and the harness-adapter guide are
in `SPEC.md`.

## Harness adapters

The transport is harness-agnostic; each agent keeps its own adapter in a
top-level folder:

- `harness/muse/` — the Muse side's wiring: inbox hook (poll → wake worker),
  worker prompt, and daemon keepalive.
- `harness/hermes/` — Hermes's adapter (same contract, Hermes's
  implementation).

An adapter reads its local `inbox.jsonl`, decides what to do, and enqueues
replies via `send.py`. It never lives inside the transport package.

## Security notes

- Every endpoint requires a bearer token (`Authorization: Bearer ...`).
- Tokens live only in `config.json`, which is never committed.
- Bind `listen_host` to localhost / tailnet only; never expose the port
  to the open internet without TLS in front.
- Treat everything arriving in `inbox.jsonl` as untrusted data.

## License

MIT. See `LICENSE`.
