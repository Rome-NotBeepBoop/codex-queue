# Contributing

Use Python 3.11+ and Git. Create a virtual environment and install `python -m pip install -e .`. Run `python -m unittest discover -s tests -v`; build with `python -m pip install build` and `python -m build`.

Keep changes incremental. The TUI and optional native scheduled checks both delegate to Worker.once; do not add a second scheduler, startup daemon, silent retry or automatic publishing. Preserve CLI compatibility and SQLite migration paths. Add mocked regression tests and Textual Pilot tests for user-visible changes, including narrow terminals and cancellation. Use temporary repositories only. Never spend real Codex allowance in tests or touch another agent's checkout.

Describe behavior, tests actually run, platform limitations and privacy implications in your contribution. Do not include tokens, account files, session histories, queued prompts, logs or databases. Run the security check before proposing publication. Report vulnerabilities privately according to SECURITY.md.

By contributing, you agree your contribution may be distributed under the project's MIT license. Retain attribution for any third-party source and document its compatible license; do not copy unknown-licensed code.

Herdr adapters must use the installed CLI's documented schema. Test with mock responses, private temporary state, and harmless Python relays only. Do not read real panes/agents or modify Herdr source/hooks during tests. Persist the no-resubmission fence before any external dispatch; unknown outcomes must never automatically retry. Test keyboard-only workflows and responsive sizes (80×24, 100×30, 120×40, 160×45), plus mouse separately. Real-integration qualification needs separate authorization and must be reported separately from mocks.

Scheduler tests must mock native commands and use temporary user homes. Never register, enable or stop a real task in tests. Test mode handoffs, native ownership refusal and execution leases. Session navigation uses synthetic metadata and must not query real Codex histories during development. Native adapters remain optional; do not claim a platform is qualified from mocked tests.
