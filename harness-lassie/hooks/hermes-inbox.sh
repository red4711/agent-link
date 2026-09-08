#!/usr/bin/env bash
# Hook polling script: wake a worker when the agent-link inbox has
# envelopes newer than the last processed offset.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

BASE="${HOME:-/home/hatch}/workspace/agent-link"
INBOX="$BASE/inbox.jsonl"
STATE_DIR="${HOME:-/home/hatch}/hooks/state/hermes-inbox"
OFFSET_FILE="$STATE_DIR/offset"
mkdir -p "$STATE_DIR"

offset="$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)"
case "$offset" in ''|*[!0-9]*) offset=0 ;; esac

if [ ! -f "$INBOX" ]; then
  silent "agent-link inbox not present" '{}'
fi

payload="$(python3 - "$INBOX" "$offset" <<'EOF'
import json, sys
inbox_path, offset = sys.argv[1], int(sys.argv[2])
msgs = []
with open(inbox_path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj.get("seq"), int) and obj["seq"] > offset:
            msgs.append({"seq": obj["seq"], "env": obj.get("envelope")})
print(json.dumps({"offset": offset, "count": len(msgs), "messages": msgs[:50]}))
EOF
)"
count="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['count'])" "$payload")"

if [ "$count" -eq 0 ]; then
  silent "no new hermes messages" '{}'
else
  wake "$count new hermes message(s)" "$payload"
fi
