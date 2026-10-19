#!/bin/sh
# Run manually on the restored Linux server. Installs files; never enables/starts services.
set -eu
[ "$(uname -s)" = Linux ] || { echo 'Linux only; local Mac tests use python -m unittest.' >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo 'Run with sudo after reviewing the script.' >&2; exit 1; }
cd "$(dirname "$0")/.."
command -v python3 >/dev/null
command -v git >/dev/null
if ! id codex-queue >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/codex-queue --shell /bin/bash codex-queue
fi
install -d -m 0755 /opt/codex-queue/source
install -d -m 0700 -o codex-queue -g codex-queue /var/lib/codex-queue/state /var/lib/codex-queue/repos /var/lib/codex-queue/.codex
install -d -m 0750 /etc/codex-queue
cp -R codex_queue pyproject.toml /opt/codex-queue/source/
python3 -m venv /opt/codex-queue/venv
/opt/codex-queue/venv/bin/python -m pip install /opt/codex-queue/source
if [ ! -e /etc/codex-queue/config.json ]; then
  install -m 0640 -o root -g codex-queue deploy/config.example.json /etc/codex-queue/config.json
fi
if [ ! -e /etc/codex-queue/queue.env ]; then
  install -m 0640 -o root -g codex-queue deploy/queue.env.example /etc/codex-queue/queue.env
fi
install -m 0644 deploy/codex-queue.service /etc/systemd/system/codex-queue.service
echo 'Installed only. Service not enabled, started, or daemon-reloaded. Execution config remains disabled on first install.'
echo 'Review existing config on upgrades. Complete docs/installation.md before any activation.'
