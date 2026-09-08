#!/usr/bin/env python3
"""Enqueue a message for the peer: send.py <type> <body> [--correlation-id ID]

Appends to outbox.jsonl; the forwarder / peer watcher delivers it.
Harness-facing but still agnostic: pure enqueue, no network, no agent logic.

Types: message, request, reply, ping, task_result
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import link

BASE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    mtype, body = argv[1], argv[2]
    correlation_id = None
    i = 3
    while i < len(argv):
        if argv[i] == "--correlation-id" and i + 1 < len(argv):
            correlation_id = argv[i + 1]
            i += 2
        else:
            i += 1
    cfg = link.load_config(BASE)
    try:
        env = link.new_envelope(cfg.get("name", "unknown"), mtype, body,
                                correlation_id=correlation_id)
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    rec = link.outbox_append(os.path.join(BASE, "outbox.jsonl"), env)
    print("queued id=%s seq=%d" % (env["id"], rec["seq"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
