#!/bin/sh
# Stops/removes only this service; preserves all jobs, auth, repos, and worktrees.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] || { echo 'Run manually with sudo on Linux.' >&2; exit 1; }
systemctl disable --now codex-queue.service
rm -f /etc/systemd/system/codex-queue.service
systemctl daemon-reload
echo 'Unit removed. /opt/codex-queue, /etc/codex-queue, user and /var/lib/codex-queue retained for review/recovery.'
