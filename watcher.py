#!/usr/bin/env python3
"""agent-link watcher: subscribes to the peer's /events SSE stream and
appends every envelope to the local inbox (idempotent: dedups by id).

Used when the peer cannot be dialed directly (or as a hot-standby second
path). Pure transport: records only, never interprets.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import link

BASE = os.path.dirname(os.path.abspath(__file__))
cfg = link.load_config(BASE)
PEER = (cfg.get("peer_base_url") or "").strip().rstrip("/")
PEER_TOKEN = (cfg.get("peer_token") or "").strip()
WATCH_PEER = bool(cfg.get("watch_peer", True))
STALL = int(cfg.get("stall_timeout_secs") or 90)
INBOX = os.path.join(BASE, "inbox.jsonl")
LOG = os.path.join(BASE, "watcher.log")
LAST_ID = os.path.join(BASE, "state", "watcher.last_id")


def log(msg):
    link.log_to(LOG, "watcher", msg)


def spawn_stream(last_id):
    cmd = [
        "curl", "-N", "-sS", "--no-buffer", "--max-time", "0",
        *link.proxy_args_for(PEER),
        "-H", "Accept: text/event-stream",
        "-H", "Authorization: Bearer " + PEER_TOKEN,
    ]
    if last_id:
        cmd += ["-H", "Last-Event-ID: " + last_id]
    cmd.append(PEER + "/events")
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)


def run_stream(proc):
    """Read one stream until stall/exit. Returns last SSE id seen."""
    last_id = None
    try:
        with open(LAST_ID) as f:
            last_id = f.read().strip() or None
    except OSError:
        pass
    cur_id, data = last_id, []
    last_byte = time.time()

    def dispatch():
        nonlocal cur_id, data
        if not data:
            return
        raw = "\n".join(data)
        data = []
        try:
            env = json.loads(raw)
        except ValueError:
            log("dropping non-JSON event payload")
            return
        if not link.valid_envelope(env):
            log("dropping invalid envelope id=%s" % env.get("id"))
            return
        rec = link.inbox_append(INBOX, env, via="watcher")
        if rec is not None:
            log("inbox seq=%d id=%s via=watcher" % (rec["seq"], env["id"]))
        try:
            with open(LAST_ID, "w") as f:
                f.write(str(cur_id or ""))
        except OSError:
            pass

    # NOTE: never select() on proc.stdout here. It is a buffered text
    # stream; select() cannot see bytes already pulled into user-space
    # buffers, so a ready fd can look empty for a whole select timeout
    # while complete lines sit unread (observed: delivery delayed until
    # the next heartbeat). A reader thread blocking on readline() plus
    # a queue delivers each line the moment it arrives.
    q = queue.Queue()

    def reader():
        try:
            for line in proc.stdout:
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)  # EOF sentinel

    threading.Thread(target=reader, daemon=True, name="sse-reader").start()

    while True:
        try:
            line = q.get(timeout=5)
        except queue.Empty:
            if proc.poll() is not None:
                raise ConnectionError("stream exited rc=%s" % proc.returncode)
            if time.time() - last_byte > STALL:
                proc.kill()
                raise ConnectionError("stream stalled")
            continue
        last_byte = time.time()
        if line is None:  # reader hit EOF
            if proc.poll() is not None:
                raise ConnectionError("stream exited rc=%s" % proc.returncode)
            raise ConnectionError("stream EOF")
        line = line.rstrip("\n").rstrip("\r")
        if line == "":
            dispatch()
        elif line.startswith(":"):
            pass  # heartbeat comment
        elif line.startswith("id:"):
            cur_id = line[3:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    return cur_id


def main():
    if not (WATCH_PEER and PEER and PEER_TOKEN):
        log("watcher disabled or peer not configured; exiting")
        return 0
    os.makedirs(os.path.join(BASE, "state"), exist_ok=True)
    with open(os.path.join(BASE, "watcher.pid"), "w") as f:
        f.write(str(os.getpid()))
    log("watching %s/events" % PEER)
    backoff = 1
    while True:
        proc = None
        try:
            last_id = None
            try:
                with open(LAST_ID) as f:
                    last_id = f.read().strip() or None
            except OSError:
                pass
            proc = spawn_stream(last_id)
            backoff = 1
            run_stream(proc)
        except Exception as e:
            log("stream error: %s; retry in %ds" % (e, backoff))
            try:
                if proc:
                    proc.kill()
            except Exception:
                pass
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()
