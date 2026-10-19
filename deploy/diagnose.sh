#!/bin/sh
set -eu
python3 --version
git --version
codex --version
/opt/codex-queue/venv/bin/codex-queue --state /var/lib/codex-queue/state --config /etc/codex-queue/config.json doctor
systemctl status codex-queue.service --no-pager || true
# Logs/prompts may contain repository information: inspect privately, redact before sharing.
