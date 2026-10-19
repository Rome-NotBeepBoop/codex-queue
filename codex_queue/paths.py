"""Portable per-user paths; no authentication or session files are inspected."""
from pathlib import Path
from platformdirs import user_config_path, user_state_path


def state_path():
    return user_state_path('codex-queue', appauthor=False)


def config_path():
    return user_config_path('codex-queue', appauthor=False) / 'config.json'
