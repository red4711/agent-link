#!/usr/bin/env python3
"""Fast-lane notifier (Hermes harness side).

Watches inbox.jsonl for appends and POSTs a wake to the local gateway
webhook (route agent-link-inbox) so incoming Lassie mail is processed in
seconds instead of waiting for the cron tick.

Transport stays pure: this never reads envelopes, only the file's max
seq. Runs as systemd user unit agent-link-notify.service.
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = "/home/hermes-agent/workspace/agent-link"
INBOX = os.path.join(BASE, "inbox.jsonl")
WATCH_DIR = BASE
WATCH_FILE = "inbox.jsonl"
SUBS = os.path.expanduser("~/.hermes/webhook_subscriptions.json")
ROUTE = "agent-link-inbox"
URL = "http://127.0.0.1:8644/webhooks/" + ROUTE
STATE = os.path.join(BASE, "state", "notify.offset")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notify.log")


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    except OSError:
        pass


def load_secret():
    with open(SUBS) as f:
        subs = json.load(f)
    if isinstance(subs, dict):
        if ROUTE in subs:
            return subs[ROUTE].get("secret", "")
        routes = subs.get("routes", {})
        if ROUTE in routes:
            return routes[ROUTE].get("secret", "")
    raise KeyError("route %s not found in %s" % (ROUTE, SUBS))


def max_seq():
    mx = 0
    try:
        with open(INBOX) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                s = o.get("seq")
                if isinstance(s, int) and s > mx:
                    mx = s
    except FileNotFoundError:
        pass
    return mx


def read_sent():
    try:
        with open(STATE) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        pass
    # First run: seed from the harness offset so already-processed mail
    # doesn't trigger a redundant wake.
    try:
        with open(os.path.join(BASE, "state", "harness.offset")) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def write_sent(v):
    d = os.path.dirname(STATE)
    os.makedirs(d, exist_ok=True)
    with open(STATE, "w") as f:
        f.write(str(v))


def notify(secret, seq):
    body = json.dumps({"seq": seq, "source": "agent-link-notify"}).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), ts.encode() + b"." + body,
                   hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Webhook-Timestamp": ts,
                 "X-Webhook-Signature-V2": sig,
                 "X-Request-ID": "agentlink-seq-%d" % seq})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            code, resp = r.status, r.read()[:200]
    except Exception as e:
        return None, str(e)[:200]
    return code, resp.decode("utf-8", "replace")


def main():
    try:
        secret = load_secret()
    except Exception as e:
        log("FATAL no webhook secret: %s" % e)
        return 1
    if not secret:
        log("FATAL empty webhook secret")
        return 1
    sent = read_sent()
    cur = max_seq()
    if cur > sent:
        code, resp = notify(secret, cur)
        log("startup catch-up seq=%d -> %s %s" % (cur, code, resp))
        if code in (200, 202):
            sent = cur
            write_sent(sent)
    log("watching %s (sent=%d)" % (INBOX, sent))
    proc = None
    try:
        proc = subprocess.Popen(
            ["inotifywait", "-m", "-e", "close_write", "--format", "%f",
             WATCH_DIR],
            stdout=subprocess.PIPE, text=True)
    except FileNotFoundError:
        proc = None
        log("inotifywait missing, falling back to 5s poll")
    pending = 0.0
    if proc is None:
        while True:
            time.sleep(5)
            cur = max_seq()
            if cur > sent:
                code, resp = notify(secret, cur)
                log("poll seq=%d -> %s %s" % (cur, code, resp))
                if code in (200, 202):
                    sent = cur
                    write_sent(sent)
        return 0
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.strip() != WATCH_FILE:
            continue
        now = time.time()
        if now - pending < 1.0:  # coalesce bursts
            continue
        pending = now
        time.sleep(1)  # let the writer finish + batch rapid appends
        cur = max_seq()
        if cur <= sent:
            continue
        for attempt in range(4):
            code, resp = notify(secret, cur)
            log("event seq=%d try=%d -> %s %s" % (cur, attempt, code, resp))
            if code in (200, 202):
                sent = cur
                write_sent(sent)
                break
            time.sleep(2 ** attempt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
