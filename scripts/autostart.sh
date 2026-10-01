#!/bin/zsh
# Start the API and the web app at login, and restart either if it stops.
#
#   scripts/autostart.sh on       # install and start both
#   scripts/autostart.sh off      # stop both and remove them from login
#   scripts/autostart.sh status   # are they running?
#
# macOS runs them as launch agents (~/Library/LaunchAgents). Output goes to
# .local/logs/, which is where to look when the phone says it cannot connect.
# Docker Desktop, Ollama and Tailscale start themselves (see the README).

set -euo pipefail

REPO="${0:A:h:h}"
LOGS="$REPO/.local/logs"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
NAMES=(api web)

plist() {
  local name=$1 command=$2 dir=$3
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.personal-os.$name</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>-c</string><string>$command</string></array>
  <key>WorkingDirectory</key><string>$dir</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <!-- At login the database may still be starting: the API exits, and is retried
       every 15 seconds until Docker is up. -->
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOGS/$name.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$name.log</string>
</dict>
</plist>
EOF
}

case "${1:-}" in
  on)
    mkdir -p "$LOGS" "$AGENTS"
    # python -m, not .venv/bin/uvicorn: that launcher is a /bin/sh script, and macOS
    # will not let a background /bin/sh read anything under ~/Desktop.
    plist api "exec .venv/bin/python -m uvicorn api.main:app" "$REPO" > "$AGENTS/com.personal-os.api.plist"
    plist web "exec npm run dev" "$REPO/web" > "$AGENTS/com.personal-os.web.plist"
    for name in $NAMES; do
      launchctl bootout "$DOMAIN/com.personal-os.$name" 2>/dev/null || true
      # Unloading finishes in the background; loading again before it has fails
      # with "Bootstrap failed: 5". Wait up to 10 seconds for it to be gone.
      for _ in {1..20}; do
        launchctl print "$DOMAIN/com.personal-os.$name" >/dev/null 2>&1 || break
        sleep 0.5
      done
      launchctl bootstrap "$DOMAIN" "$AGENTS/com.personal-os.$name.plist"
    done
    echo "On. They start at every login. Logs: $LOGS"
    echo "Don't also start uvicorn or npm run dev by hand: the ports would clash."
    ;;
  off)
    for name in $NAMES; do
      launchctl bootout "$DOMAIN/com.personal-os.$name" 2>/dev/null || true
      rm -f "$AGENTS/com.personal-os.$name.plist"
    done
    echo "Off. Start them by hand again (README, Day to day)."
    ;;
  status)
    for name in $NAMES; do
      if launchctl print "$DOMAIN/com.personal-os.$name" 2>/dev/null | grep -q "state = running"; then
        echo "$name: running"
      else
        echo "$name: not running (see $LOGS/$name.log)"
      fi
    done
    ;;
  *)
    echo "usage: scripts/autostart.sh on|off|status" >&2
    exit 2
    ;;
esac
