#!/usr/bin/env bash
# Change detector for the agent-link inbox (Hermes side).
# Prints the highest inbox seq; the scheduler wakes the agent only when
# this output changes, i.e. only when new mail arrives. Deterministic:
# no timestamps, no counts of anything but seq.
INBOX="/home/hermes-agent/workspace/agent-link/inbox.jsonl"
python3 - "$INBOX" <<'EOF'
import json, os, sys
p = sys.argv[1]
if not os.path.exists(p):
    print("max_seq=0")
    raise SystemExit(0)
mx = 0
with open(p) as f:
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
print("max_seq=%d" % mx)
EOF
