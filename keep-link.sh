#!/usr/bin/env bash
# Supervise the agent-link processes: server (always), watcher and
# forwarder (only when the peer is configured for those paths).
set -u
BASE="$HOME/workspace/agent-link"

peer_url="$(python3 -c "import json;print(json.load(open('$BASE/config.json')).get('peer_base_url','').strip())" 2>/dev/null)"
peer_token="$(python3 -c "import json;print(json.load(open('$BASE/config.json')).get('peer_token','').strip())" 2>/dev/null)"
watch_peer="$(python3 -c "import json;print('1' if json.load(open('$BASE/config.json')).get('watch_peer') else '')" 2>/dev/null)"
egress="$(python3 -c "import json;print(' '.join(json.load(open('$BASE/config.json')).get('egress') or []))" 2>/dev/null)"
self_token="$(python3 -c "import json;print(json.load(open('$BASE/config.json')).get('self_token','').strip())" 2>/dev/null)"

start_if_dead() { # $1=name $2=script
  local name="$1"
  local script="$2"
  local pidfile="$BASE/$name.pid"
  local pid=""
  [ -f "$pidfile" ] && pid="$(cat "$pidfile" 2>/dev/null)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q "$script"; then
    return 0
  fi
  cd "$BASE" && setsid nohup python3 "$BASE/$script" >>"$BASE/$name.log" 2>&1 < /dev/null &
  echo "started agent-link $name (pid $!)"
}

run_once() {
  [ -n "$self_token" ] && start_if_dead server server.py
  if [ -n "$peer_url" ] && [ -n "$peer_token" ] && [ -n "$watch_peer" ]; then
    start_if_dead watcher watcher.py
  fi
  if [ -n "$peer_url" ] && [ -n "$peer_token" ] && [[ "$egress" == *"post"* ]]; then
    start_if_dead forwarder forwarder.py
  fi
}

if [ "${1:-}" = "--supervise" ]; then
  # Foreground supervisor for systemd: keep daemons alive, re-reading
  # config each tick so token/peer changes are picked up without restarts.
  while true; do
    peer_url="$(python3 -c "import json;print(json.load(open('$BASE/config.json')).get('peer_base_url','').strip())" 2>/dev/null)"
    peer_token="$(python3 -c "import json;print(json.load(open('$BASE/config.json')).get('peer_token',''))" 2>/dev/null)"
    watch_peer="$(python3 -c "import json;print('1' if json.load(open('$BASE/config.json')).get('watch_peer') else '')" 2>/dev/null)"
    egress="$(python3 -c "import json;print(' '.join(json.load(open('$BASE/config.json')).get('egress') or []))" 2>/dev/null)"
    self_token="$(python3 -c "import json;print(json.load(open('$BASE/config.json')).get('self_token',''))" 2>/dev/null)"
    run_once
    sleep 30
  done
else
  run_once
fi
