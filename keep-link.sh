#!/usr/bin/env bash
# Supervise the agent-link processes: server (always), watcher and
# forwarder (only when the peer is configured for those paths).
set -u
BASE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

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

# Recreate the systemd supervisor after a reboot wipes /etc. Runs only as
# root with systemd present; otherwise a no-op. Idempotent: if the unit
# file exists, just make sure the service is active.
ensure_systemd() {
  [ "$(id -u)" = "0" ] || return 0
  command -v systemctl >/dev/null 2>&1 || return 0
  local unit="/etc/systemd/system/agent-link.service"
  local envfile="/etc/agent-link/environment"
  if [ ! -f "$unit" ]; then
    mkdir -p /etc/agent-link
    chmod 700 /etc/agent-link
    # Proxy settings for tailnet egress; derived at runtime, never printed.
    {
      for v in HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy; do
        val="${!v:-}"
        [ -n "$val" ] && printf '%s=%s\n' "$v" "$val"
      done
    } > "$envfile.tmp"
    chmod 600 "$envfile.tmp"
    if grep -q . "$envfile.tmp" 2>/dev/null || [ ! -f "$envfile" ]; then
      mv "$envfile.tmp" "$envfile"
    else
      rm -f "$envfile.tmp"
    fi
    cat > "$unit" <<EOF
[Unit]
Description=agent-link daemon supervisor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$envfile
ExecStart=/bin/bash $BASE/keep-link.sh --supervise
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    chmod 644 "$unit"
    systemctl daemon-reload 2>/dev/null || true
  fi
  systemctl is-active --quiet agent-link.service 2>/dev/null \
    || systemctl start agent-link.service 2>/dev/null || true
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
  # No-arg keepalive: prefer the systemd supervisor when we can manage it
  # (root); otherwise fall back to direct process supervision.
  ensure_systemd
  if command -v systemctl >/dev/null 2>&1 \
     && systemctl is-active --quiet agent-link.service 2>/dev/null; then
    exit 0
  fi
  run_once
fi
