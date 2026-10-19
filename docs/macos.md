# macOS installation

Install the reviewed wheel using pipx, uv tool install or a virtual environment. Apple Silicon is locally mock-tested; Intel compatibility is a target awaiting qualification. Launch `codex-queue` in Ghostty or another Textual terminal.

The installed development environment can be updated with `python -m pip install -e .` from the existing source repository. Use its `.venv/bin/python` if installed there. No shell startup files are changed by the package.

The TUI uses platformdirs for configuration/state paths. Override them with CODEX_QUEUE_CONFIG and CODEX_QUEUE_STATE if migrating a previous installation; select your original state to preserve jobs. Startup never loads launchd. Optional Scheduler Management supports explicit Configure, Install disabled and Enable actions for a state-specific per-user LaunchAgent. See [scheduler guide](schedulers.md). Earlier disabled launchd preparation files are historical and are not automatically adopted.

See ../QUICKSTART.md. Real execution remains disabled unless explicitly enabled in settings/config, and only an explicitly started watcher/CLI worker or separately enabled managed native scheduler runs jobs. Native activation is not yet qualified. The mock test suite and installed command are verified; real account usage and session execution remain untested.
