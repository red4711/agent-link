#!/usr/bin/env python3
"""agent-link server: HTTP endpoints for the peer link.

  GET  /health   -> {"ok": true}
  POST /message  -> validate auth + envelope, append to inbox, 200
  GET  /events   -> SSE stream of the outbox (for the peer's watcher)

Harness-agnostic: auth, validation, queue appends. No agent logic.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import link

BASE = os.path.dirname(os.path.abspath(__file__))
cfg = link.load_config(BASE)
SELF_TOKEN = (cfg.get("self_token") or "").strip()
HOST = cfg.get("listen_host", "127.0.0.1")
PORT = int(cfg.get("listen_port", 8787))
INBOX = os.path.join(BASE, "inbox.jsonl")
OUTBOX = os.path.join(BASE, "outbox.jsonl")
LOG = os.path.join(BASE, "server.log")


def log(msg):
    link.log_to(LOG, "server", msg)


class Handler(BaseHTTPRequestHandler):
    server_version = "agent-link/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    def authed(self):
        if not SELF_TOKEN:
            return False
        return self.headers.get("Authorization", "") == "Bearer " + SELF_TOKEN

    def do_GET(self):
        path = urlparse(self.path).path
        if not self.authed():
            return self._json(401, {"ok": False, "error": "unauthorized"})
        if path == "/health":
            return self._json(200, {"ok": True})
        if path == "/events":
            return self.serve_events()
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if not self.authed():
            return self._json(401, {"ok": False, "error": "unauthorized"})
        if path != "/message":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > 131072:
            return self._json(400, {"ok": False, "error": "bad length"})
        try:
            obj = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"ok": False, "error": "bad json"})
        if not link.valid_envelope(obj):
            return self._json(400, {"ok": False, "error": "bad envelope"})
        rec = link.inbox_append(INBOX, obj, via="server")
        return self._json(200, {"ok": True, "id": obj["id"],
                                "duplicate": rec is None})

    def serve_events(self):
        # SSE: headers, then `id: <outbox-seq>\\ndata: <envelope>\\n\\n`
        # per record, `: ping` heartbeat every 25s, stream until close.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            offset = int((self.headers.get("Last-Event-ID") or "").strip() or 0)
        except ValueError:
            offset = 0
        ping_at = time.time()
        try:
            while True:
                offset = self.push_new(offset)
                if time.time() - ping_at >= 25:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    ping_at = time.time()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def push_new(self, offset):
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
                        offset = rec["seq"]
                        payload = "id: %d\ndata: %s\n\n" % (
                            offset, json.dumps(rec["envelope"]))
                        self.wfile.write(payload.encode())
            self.wfile.flush()
        except FileNotFoundError:
            pass
        return offset


def main():
    if not SELF_TOKEN:
        log("self_token not configured; exiting")
        return 0
    with open(os.path.join(BASE, "server.pid"), "w") as f:
        f.write(str(os.getpid()))
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    log("listening on %s:%d" % (HOST, PORT))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
