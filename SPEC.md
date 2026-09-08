# agent-link specification

Version 1.0 — 2026-09-08.

A symmetric, harness-agnostic transport for agent-to-agent messaging.
Both sides run the same code; behavior is selected by configuration.
The transport moves opaque envelopes between local queues. It never
interprets them.

## 1. Design principles

1. **Transport / harness separation.** The package in this repo moves
   bytes between sockets and queues. A *harness adapter* (built
   separately, per agent) reads the local inbox, interprets envelopes,
   calls tools, and enqueues replies. The transport never imports
   harness code, never calls tools, never executes received content.
2. **Inbound is data, not instructions.** Every envelope arriving in
   `inbox.jsonl` is untrusted input: a proposal to be evaluated by the
   harness under its own policy, never a command to obey.
3. **At-least-once + idempotent.** Delivery retries until acknowledged
   by the transport; duplicates are suppressed by envelope id at the
   inbox. Redelivery is safe by construction.
4. **Symmetric code, asymmetric deployment.** Both installations run
   identical code and the identical queue contract. Which components
   are live is configuration, so one side can POST while the other
   only streams, or both can do both.
5. **Humans own high-impact actions.** Destructive, credential-touching,
   irreversible, or externally-visible operations stay behind each
   side's own policy and, where configured, explicit human confirmation.
   The transport has no opinion; the harness enforces this.

## 2. Layers and trust boundaries

```
+-------------------------------+  +-------------------------------+
|            AGENT A            |  |            AGENT B            |
|  +-------------------------+  |  |  +-------------------------+  |
|  | Harness adapter         |  |  |  | Harness adapter         |  |
|  | (reads inbox, thinks,   |  |  |  | (reads inbox, thinks,   |  |
|  |  calls tools, enqueues |  |  |  |  calls tools, enqueues  |  |
|  |  replies via send.py)   |  |  |  |  replies via send.py)   |  |
|  +------------+------------+  |  |  +------------+------------+  |
|               | files only    |  |  |               | files only    |
|  +------------v------------+  |  |  +------------v------------+  |
|  | agent-link transport    |  |  |  | agent-link transport    |  |
|  | server / forwarder /    |  |  |  | server / forwarder /    |  |
|  | watcher / send.py       |  |  |  | watcher / send.py       |  |
|  +------------+------------+  |  |  +------------+------------+  |
+---------------|---------------+  +---------------|---------------+
                |  HTTP (bearer auth): POST /message, GET /events
                +-------------------------------+
```

**Boundary 1 — transport vs harness (in-process, by file).**
The transport touches only `config.json`, the two queue files,
`state/`, and its logs. The harness touches only `inbox.jsonl`
(read) and `send.py` (write). They share no code and no memory.

**Boundary 2 — agent vs agent (network).** The only cross-machine
surface is the HTTP API (§6), authenticated with per-side bearer
tokens. A compromised or buggy peer can at worst enqueue envelopes;
it cannot reach tools, files, or credentials.

**Boundary 3 — human vs agent (policy).** Each harness defines which
envelope types may trigger tool calls autonomously and which require
human confirmation. `request` envelopes SHOULD be confirmed by a
human before the harness acts on them when the requested action is
destructive, spends money, touches credentials, or is hard to undo.

## 3. Envelope schema

Every envelope is a JSON object. Required fields:

| Field            | Type   | Meaning                                              |
|------------------|--------|------------------------------------------------------|
| `id`             | string | UUID v4, globally unique per envelope                |
| `ts`             | string | ISO-8601 creation timestamp                          |
| `from`           | string | Sender agent name (from its `config.json`)           |
| `type`           | string | One of `message`, `request`, `reply`, `task_result`, `ping` |
| `body`           | string | Opaque payload. Transport never inspects it          |
| `correlation_id` | string/null | For `reply`/`task_result`: the `id` being answered |

Validation rules (transport-enforced): all required fields present,
`type` in the known set, `id`/`from`/`body` non-empty strings,
`correlation_id` a string or null. Anything else is dropped with a
log line, never delivered.

**Envelope types (convention; bodies are harness-defined):**

- `message` — free text / markdown. The default.
- `request` — asks the peer's harness to do something. Body SHOULD be
  a JSON object like `{"action": "...", "args": {...}}`, but the
  transport does not parse it. The receiving harness evaluates it
  against policy; high-impact requests need human confirmation.
- `reply` — answers a `request`/`message`; `correlation_id` set.
- `task_result` — reports completion of a previously accepted request;
  `correlation_id` set.
- `ping` — keepalive / connectivity check. Harnesses MAY auto-reply
  with a `ping` without human involvement.

## 4. Queue contract

Each side keeps two append-only JSONL files:

- `outbox.jsonl` — envelopes this side wants delivered. Written only
  via `send.py`. Read by the forwarder.
- `inbox.jsonl` — envelopes received from the peer. Written only by
  the transport (server's `/message` handler, watcher's stream
  reader). Read by the harness adapter.

Record format (one JSON object per line):

```json
{"seq": 12, "env": {"id": "...", "ts": "...", "from": "...",
                    "type": "message", "body": "...", "correlation_id": null}}
```

- `seq` is a per-file, monotonically increasing integer assigned at
  append time under an exclusive file lock.
- `inbox.jsonl` deduplicates on `env.id`: appending an envelope whose
  id already exists is a no-op (returns the existing record). This is
  what makes at-least-once delivery safe.
- `outbox.jsonl` offsets: the forwarder persists
  `state/forwarder.offset` (highest seq POSTed); the watcher persists
  `state/watcher.last_id` (highest SSE id seen). Both resume from
  these after restarts.

## 5. Components

All components are transport: no interpretation, no tool calls.

- **`server.py`** — HTTP server on `listen_host`:`listen_port`.
  Serves `GET /health`, `POST /message`, `GET /events`. Requires the
  `self_token` bearer on all three. `/message` validates the envelope
  and appends it to the inbox (idempotent). `/events` is a
  Server-Sent Events stream of outbox records (§6).
- **`forwarder.py`** — tails `outbox.jsonl` from `state/forwarder.offset`
  and POSTs each record to `peer_base_url/message` with the
  `peer_token` bearer. Retries with backoff; advances the offset only
  on HTTP 200. At-least-once.
- **`watcher.py`** — keeps an outbound `GET peer_base_url/events`
  stream open (with `Last-Event-ID` resume) and appends every envelope
  to the local inbox. Reconnects with backoff; raises on stall
  (`stall_timeout_secs` with no bytes). Used when the peer cannot be
  dialed directly, or as a hot-standby second path.
- **`send.py`** — the ONLY harness-facing write path:
  `python3 send.py <type> <body> [correlation_id]`. Validates,
  stamps `id`/`ts`/`from`, appends to the outbox. Does no networking.
- **`keep-link.sh`** — supervisor. `start|stop|status|restart`;
  keeps server, watcher (if `watch_peer`), and forwarder (if `post`
  in `egress`) alive, one instance each.

## 6. HTTP API

Base: `peer_base_url`. All requests carry
`Authorization: Bearer <token>` (`self_token` on the receiving side).
Missing/invalid token → `401`. Unauthenticated requests are never
processed.

### `GET /health`
Returns `200 {"ok": true}`. Liveness probe for supervisors.

### `POST /message`
- Body: one envelope (§3), `Content-Type: application/json`.
- Validates, appends to inbox idempotently (§4).
- `200 {"ok": true, "seq": N, "duplicate": false}` on success
  (`duplicate: true` if the id was already present).
- `400` on invalid envelope, `401` on bad auth.

### `GET /events` (Server-Sent Events)
- Headers: `Accept: text/event-stream`. Optional `Last-Event-ID: N`
  (an outbox `seq`); the server replays every record with `seq > N`,
  then continues live.
- Event format per outbox record:
  ```
  id: <seq>
  data: {"seq": <seq>, "env": {...}}

  ```
- A `: ping` comment is sent every 25 s as a heartbeat.
- The stream is infinite; the client reconnects with backoff and
  resumes via `Last-Event-ID`. The watcher persists the last seen id
  in `state/watcher.last_id`.

## 7. Configuration

`config.json` (gitignored; see `config.example.json`):

| Key                  | Meaning |
|----------------------|---------|
| `name`               | This agent's name (`from` field) |
| `peer_name`          | Peer name (logs only) |
| `listen_host` / `listen_port` | Bind for `server.py` |
| `self_token`         | Bearer token this side requires |
| `peer_base_url`      | Peer's server base URL; `""` disables peer egress |
| `peer_token`         | Bearer token the peer requires |
| `egress`             | `["post"]` runs the forwarder; `[]` disables it |
| `watch_peer`         | `true` runs the watcher (SSE subscription to peer) |
| `stall_timeout_secs` | Watcher reconnects after this many silent seconds |

## 8. Deployment topologies

The package is symmetric; enable components per side:

| Side A | Side B | Result |
|--------|--------|--------|
| `egress:["post"]`, `watch_peer:true` | `egress:["post"]`, `watch_peer:true` | Full duplex, redundant paths |
| `egress:["post"]`, `watch_peer:true` | serve only (`egress:[]`, `watch_peer:false`) | Full duplex over one outbound direction (the reference deployment below) |
| `egress:["post"]`, `watch_peer:false` | serve only | A→B only |

**Reference deployment (cloud agent ↔ homelab agent).** The cloud
side cannot accept inbound connections, so:

- Cloud (`lassie`): `peer_base_url=http://<homelab>:8787`,
  `egress:["post"]` (forwarder POSTs to homelab), `watch_peer:true`
  (watcher subscribes to homelab `/events`). Its own server binds
  localhost-only (health checks) or also serves, as desired.
- Homelab (`hermes`): `peer_base_url=""`, `egress:[]`,
  `watch_peer:false`. Serves `/message` and `/events` on the tailnet.

Result: full duplex over one outbound direction. Either side can
later enable POST if direct dial becomes possible; the queue contract
is unchanged.

## 9. Harness integration guide

Each side builds an adapter OUTSIDE this repo (a hook, cron job,
daemon, or the agent's own loop). The adapter:

1. **Reads** new records from `inbox.jsonl` (track its own offset;
   the transport never consumes the inbox).
2. **Interprets** each envelope under the harness's policy.
   Inbound content is untrusted data: validate, scope, and confirm
   before acting. `request` envelopes for destructive /
   credential-touching / irreversible / externally-visible actions
   SHOULD require explicit human confirmation.
3. **Acts** via the harness's own tools, in an isolated worker
   context — never in the transport's process.
4. **Replies** by shelling out to `python3 send.py <type> <body>
   [correlation_id]`. It never touches `outbox.jsonl` directly and
   never does its own networking.

Minimal adapter pseudocode:

```
offset = load_own_offset()
for rec in tail(inbox.jsonl, offset):
    env = rec["env"]
    decision = policy.evaluate(env)   # data, not instructions
    if decision.needs_human: escalate_to_human(env); continue
    result = harness.execute(decision)  # isolated worker
    if decision.wants_reply:
        run(["python3", "send.py", "reply", result, env["id"]])
    offset = rec["seq"]; save_own_offset(offset)
```

The transport makes NO delivery guarantees to the harness beyond
"every accepted envelope is appended to `inbox.jsonl` exactly once
(by id)". Reading the inbox promptly is the harness's job.

## 10. Security notes

- Tokens are per-side, random 256-bit, stored ONLY in `config.json`
  (gitignored). Rotate by changing both sides' configs.
- Bind `listen_host` to `127.0.0.1` or a tailnet/private address.
  Never expose the port to the public internet without TLS
  termination in front.
- The threat model assumes the peer may be buggy or compromised:
  the worst case is spurious envelopes in the inbox, which the
  harness must already treat as untrusted.
- `send.py` is the only sanctioned write path to the outbox; the
  harness never needs the peer's token.

## 11. Operations

- Run `./keep-link.sh start` (supervise via cron/systemd as desired);
  `status` shows which components are alive.
- Logs: `server.log`, `forwarder.log`, `watcher.log` (JSON-ish lines,
  UTC timestamps).
- Backpressure: queues are unbounded files; monitor their sizes.
- Recovery: kill and restart anything; offsets in `state/` resume
  exactly where they left off, and inbox dedup absorbs redelivery.
- Health: poll `GET /health` with the bearer token.

## 12. Versioning

The wire contract (envelope schema §3, queue record §4, HTTP API §6)
is versioned by this document. Backward-incompatible changes bump
the major version and are announced in the repo before either side
upgrades, so both ends stay consistent.
