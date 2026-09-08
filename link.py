"""Shared, harness-agnostic helpers for the agent-link package.

Boundary rule: this module (and server.py / watcher.py / forwarder.py /
send.py) must never import harness code, call agent tools, or interpret
message bodies. It moves bytes between queues and sockets. The harness
adapter owns all meaning. That is the line.
"""
import fcntl
import json
import os
import urllib.parse
import uuid
from datetime import datetime, timezone

ENVELOPE_TYPES = {"message", "request", "reply", "task_result", "ping"}
MAX_BODY = 65536


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def log_to(path, comp, msg):
    try:
        with open(path, "a") as f:
            f.write("%s [%s] %s\n" % (utcnow(), comp, msg))
    except OSError:
        pass


def load_config(base):
    with open(os.path.join(base, "config.json")) as f:
        return json.load(f)


def new_envelope(name, mtype, body, correlation_id=None, msg_id=None):
    if mtype not in ENVELOPE_TYPES:
        raise ValueError("unknown envelope type: %r" % (mtype,))
    if not isinstance(body, str):
        raise ValueError("body must be a string")
    if len(body) > MAX_BODY:
        raise ValueError("body exceeds %d chars" % MAX_BODY)
    env = {
        "id": msg_id or str(uuid.uuid4()),
        "ts": utcnow(),
        "from": name,
        "type": mtype,
        "body": body,
    }
    if correlation_id:
        env["correlation_id"] = correlation_id
    return env


def valid_envelope(obj):
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("id"), str) and bool(obj["id"])
        and isinstance(obj.get("from"), str) and bool(obj["from"])
        and obj.get("type") in ENVELOPE_TYPES
        and isinstance(obj.get("body"), str)
        and len(obj["body"]) <= MAX_BODY
    )


def _locked(path, fn):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            return fn(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read_records(f):
    f.seek(0)
    max_seq, ids = 0, set()
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        s = o.get("seq", 0)
        if isinstance(s, int) and s > max_seq:
            max_seq = s
        env = o.get("envelope") or {}
        if isinstance(env.get("id"), str):
            ids.add(env["id"])
    return max_seq, ids


def outbox_append(path, envelope):
    """Append an envelope to the outbox. Returns the queue record."""
    def _do(f):
        max_seq, _ = _read_records(f)
        rec = {"seq": max_seq + 1, "queued_at": utcnow(), "envelope": envelope}
        f.write(json.dumps(rec) + "\n")
        f.flush()
        os.fsync(f.fileno())
        return rec
    return _locked(path, _do)


def inbox_append(path, envelope, via):
    """Append to the inbox unless this envelope id is already present
    (idempotent inbox: at-least-once delivery is safe). Returns the
    record, or None on duplicate."""
    def _do(f):
        max_seq, ids = _read_records(f)
        if envelope["id"] in ids:
            return None
        rec = {"seq": max_seq + 1, "received_at": utcnow(),
               "via": via, "envelope": envelope}
        f.write(json.dumps(rec) + "\n")
        f.flush()
        os.fsync(f.fileno())
        return rec
    return _locked(path, _do)


def read_offset(path):
    try:
        with open(path) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def write_offset(path, value):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w") as f:
        f.write(str(value))


def proxy_args_for(url):
    """curl args to reach url. Local peers bypass the tunnel proxy."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return ["--noproxy", "*"]
    hp = os.environ.get("HTTPS_PROXY", "")
    if not hp:
        return []
    u = urllib.parse.urlsplit(hp)
    args = ["-x", "http://%s:3130" % (u.hostname or "hatch-egress-proxy")]
    if u.username:
        creds = u.username + ((":" + u.password) if u.password else "")
        args += ["-U", creds]
    return args
