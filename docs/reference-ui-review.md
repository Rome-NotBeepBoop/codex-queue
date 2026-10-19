# CODEX/QUEUE reference redesign review

The supplied target screenshot guides the dashboard composition. This is a Textual UI change; scheduling, quota eligibility, persistence, locking, session discovery/resumption and execution backends retain their existing implementations.

## Visual comparison

| Target | Implementation |
| --- | --- |
| Left brand, muted version, right status | Shared CODEX/QUEUE header uses installed package metadata. Local First is the default; Herdr Connected requires a successful explicit check within 120 seconds. |
| Five horizontal numbered destinations | Dashboard, Projects, Queue, Watch and Settings; visible keyboard focus and subtle surface selection. |
| One account box with three columns | Five-hour allowance, weekly allowance and local reset time share a single border. |
| Thin progress tracks | Character-cell tracks show supplied usage, amber when exhausted. Unknown usage leaves an empty track and explicit Unknown label. |
| Balanced content panels | Quick Actions uses approximately 56%; Queue State uses approximately 44%. |
| Compact terminal footer | Worker state and keyboard hints; shorter hints on narrow terminals. |
| Secondary destinations retained | More… opens history, usage, scheduler management, extensions, settings and exit. |
| Restrained dark palette | Centralized color variables cover panels, forms, lists, dialogs, focus and scrollbars. |

Dashboard OS scheduler text describes the queue's persisted execution permission, not a fresh native scheduler probe. Enabled permission is labelled **PERMITTED**; Scheduler Management remains the place to check native status. Opening the dashboard does not contact Codex or Herdr, discover sessions or start workers.

## Actual renders

These are Textual screen exports from a controlled headless test, converted from SVG to PNG. They are actual widget renders, not browser mockups or screenshots from Ghostty/Windows Terminal. No terminal emulator or Linux/Windows rendering qualification is claimed. Exported window chrome is added by Textual's screenshot exporter.

- [Baseline before this redesign](screenshots/reference-baseline.png)
- [120×35 dashboard, unknown usage](screenshots/reference-dashboard-120x35.png)
- [120×35 synthetic usage](screenshots/reference-dashboard-mock-usage.png) — 0% usage remaining / 37% are mocked verification values, not this user's account.
- [80×24 initial viewport](screenshots/reference-dashboard-80x24.png)
- [80×24 focused lower controls](screenshots/reference-dashboard-80x24-scrolled.png)
- [100×30 dashboard](screenshots/reference-dashboard-100x30.png)
- [160×45 dashboard](screenshots/reference-dashboard-160x45.png)
- [Projects](screenshots/reference-projects-120x35.png), [prompt editor](screenshots/reference-prompt-120x35.png), [queue](screenshots/reference-queue-120x35.png), [settings](screenshots/reference-settings-120x35.png), [Herdr settings](screenshots/reference-herdr-120x35.png), [help dialog](screenshots/reference-help-120x35.png).

The renders were visually inspected and refined to correct focused-tab clipping, progress-track clipping, version spacing, narrow navigation width and clipped queue-button labels in the stacked layout. Terminal cells limit font size, border weight, icon appearance and spacing precision. The normal dashboard matches the reference's hierarchy and proportions; it is not a pixel-identical browser reproduction. At 80 columns, panels stack and keyboard focus scrolls controls into view. At 100×30 the lower actions may require scrolling. At 120×35 the complete dashboard fits without scrolling. Taller terminals retain empty space below the compact panels instead of stretching the panels artificially.

## Files changed for this UI task

- `codex_queue/ui_components.py`: shared brand header, primary tabs, allowance metrics, usage tracks, footer and palette.
- `codex_queue/tui.py`: dashboard composition, centralized styles, navigation routing and status presentation.
- `codex_queue/navigation.py`, `scheduler_ui.py`, `extensions_ui.py`: shared header; shortcut help and checked connection presentation.
- `tests/test_interactive.py`, `tests/test_herdr.py`: existing UI assertions/routes updated for horizontal navigation.
- `tests/test_reference_ui.py`: geometry, focus traversal, keyboard/mouse routes, text-entry protection, empty/unknown state, usage tracks, remaining allowance and four sizes.
- `scripts/render_reference_ui.py`: reproducible rendering with temporary empty state and no live provider calls.
- `README.md`, `QUICKSTART.md`, `docs/keyboard.md`, this review, and `docs/screenshots/reference-*`: documentation and render evidence.

Preexisting repository changes were retained. This file describes the latest UI task, not every uncommitted change in the repository.

## Validation

- Complete existing and new suite: **158 tests passed** on macOS/Python 3.14.
- After final spacing/focus refinements: **9 reference UI tests passed**.
- After checked-connection presentation changes: **28 Herdr tests passed**, using mocks and harmless children only.
- Package CLI help, Python compilation and Git whitespace checks passed.

Run the complete suite with `.venv/bin/python -m unittest discover -s tests -q`. It uses mocked usage, simulated writers and disposable repositories. No real Codex prompts or live Herdr dispatches are needed.

Coverage includes horizontal arrows/Enter, number shortcuts from the dashboard, mouse selection, Tab/Shift+Tab, help/Escape, secondary menu keyboard routes, prompt/input protection, dialog cancellation, responsive focus scrolling, CLI compatibility, existing scheduling and execution safeguards. Watcher tests use fixture/simulation execution only.

Regenerate SVGs with `.venv/bin/python scripts/render_reference_ui.py`. SVG files can be viewed directly; PNG conversion is optional and requires an SVG renderer. Never replace synthetic fixture paths with an externally owned repository.

## Launch locally

Close the old queue TUI and reopen it:

```sh
codex-queue
```

The local editable installation loads this source. If the command is unavailable, use the repository's existing virtual environment:

```sh
cd /path/to/codex-queue
.venv/bin/codex-queue
```

Use Left/Right then Enter for tabs, or 1–5 from the dashboard. Tab reaches Quick Actions, View queue and More…. Escape returns; ? opens help outside text fields. Number/help shortcuts do not intercept text input. Starting the TUI starts no monitoring or jobs. Real execution and unattended scheduling remain disabled until explicitly authorized.

## Fixed top-section correction

The latest screenshot correction keeps the title and navigation at the same top coordinates across Dashboard, Projects, Queue, Watch and Settings. Settings is now a page below the common header, rather than a modal covering it. The active tab changes, while the branding, connection indicator and tab positions remain consistent. Primary pages are retained while the app is open, preserving editor contents and wizard steps across tab switches. Successful submission clears the cached project draft.

The correction passed the complete 153-test suite, followed by focused interactive and layout checks after the final focus/selection refinements.

The brand uses bold full-width letters and a spaced slash to increase its prominence without a large ASCII banner. A terminal still cannot reproduce the browser mockup's independently sized font. Quick actions now have separate label/description groups without the previous solid highlighted block; queue rows use thin dividers.

Watch is a passive tab with a separate Start monitoring control. Both real and simulated monitoring require that action; changing tabs never starts execution.

Actual tab captures: [Projects](screenshots/reference-tab-2-120x35.png), [Queue](screenshots/reference-tab-3-120x35.png), [Watch](screenshots/reference-tab-4-120x35.png), [Settings](screenshots/reference-tab-5-120x35.png). Automated checks compare header/nav geometry on each tab, verify the active marker, preserve a typed draft through multiple switches, and confirm zero provider calls and no watcher controller.
