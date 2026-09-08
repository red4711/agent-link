#!/usr/bin/env python3
"""agent-link forwarder: delivers outbox envelopes to the peer.

egress=["post"]   -> POST each envelope to peer_base_url/message,
                     acked with HTTP 2xx, retried with backoff (at-least-once).
egress=["stream"] -> passive; the peer's watcher pulls our /events stream,
                     so there is nothing to push. Exits quietly.

Pure transport: no interpretation of bodies.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import link

BASE = os.path.dirname(os.path.abspath(__file__))
cfg = link.load_config(BASE)
PEER = (cfg.get("peer_base_url") or "").strip().rstrip("/")
PEER_TOKEN = (cfg.get("peer_token") or "").strip()
EGRESS = cfg.get("egress") or ["post"]
OUTBOX = os.path.join(BASE, "outbox.jsonl")
LOG = os.path.join(BASE, "forwarder.log")
OFFSET = os.path.join(BASE, "state", "forwarder.offset")


def log(msg):
    link.log_to(LOG, "forwarder", msg)


def post_envelope(env):
    cmd = [
        "curl", "-sS", "--max-time", "30", "-w", "\n%{http_code}",
        *link.proxy_args_for(PEER),
        "-H", "Content-Type: application/json",
        "-H", "Authorization: Bearer " + PEER_TOKEN,
        "-d", json.dumps(env),
        PEER + "/message",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    code = (p.stdout or "").strip().rsplit("\n", 1)[-1]
    return p.returncode == 0 and code.startswith("2")


def outbox_after(offset):
    recs = []
    try:
        with open(OUTBOX) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("seq", 0) > offset:
                    recs.append(rec)
    except FileNotFoundError:
        pass
    return recs


def main():
    if "post" not in EGRESS:
        log("egress=%s: nothing to push (peer pulls our stream); exiting" % EGRESS)
        return 0
    if not (PEER and PEER_TOKEN):
        log("peer not configured; exiting")
        return 0
    os.makedirs(os.path.join(BASE, "state"), exist_ok=True)
    with open(os.path.join(BASE, "forwarder.pid"), "w") as f:
        f.write(str(os.getpid()))
    offset = link.read_offset(OFFSET)
    log("forwarder started (offset=%d peer=%s)" % (offset, PEER))
    while True:
        recs = outbox_after(offset)
        if not recs:
            time.sleep(2)
            continue
        for rec in recs:
            env = rec["envelope"]
            backoff = 1
            while True:
                try:
                    ok = post_envelope(env)
                except Exception as e:
                    log("post error id=%s: %s" % (env.get("id"), e))
                    ok = False
                if ok:
                    break
                log("post failed id=%s, retry in %ds" % (env.get("id"), backoff))
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            offset = rec["seq"]
            link.write_offset(OFFSET, offset)
            log("delivered id=%s seq=%d" % (env.get("id"), offset))


if __name__ == "__main__":
    main()
