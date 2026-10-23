# Theme and visual refresh — 2026-10-23

User direction: provide both light and dark choices, improve typography,
spacing and cards. Continue the existing Vue workbench; change frontend only.

Design: use the repair workbench's teal identity, clear reading surfaces and
code panes. Avoid decorative dashboard metrics or new navigation. Alternatives
considered: light-only refinement, dark-only console, selectable themes; the user
chose selectable themes. Existing features and public data contracts stay intact.

Palette: light canvas #eef3f6, paper #ffffff, ink #203646, teal #087e87;
dark canvas #101c25, paper #192a36, ink #e5eef3, teal #7ad8d4. Muted copy,
borders, inputs, status messages and code panes have theme-specific tokens.
Typography: local Microsoft YaHei / PingFang SC / Segoe UI for Chinese/Latin
interface, Cascadia Code / Consolas for code. Body 14px, heading 32px, supporting
text 12–13px; mobile headings 26px. No network font downloads.
Layout: left-aligned workbench, restrained panel shadows, generous form spacing,
distinct code panes, experience details separated into readable fields. Theme
controls live in the top bar and wrap on narrow screens.

Implementation plan (writing-plans; execute inline):
- [x] Add tests for initial system preference, persisted manual choice, invalid
  or unavailable storage and App theme buttons with aria-pressed states.
- [x] Add src/features/theme.ts; apply theme via document root data-theme.
  App.vue exposes labeled light/dark buttons; user's selection persists locally.
- [x] Refine styles.css with semantic tokens for both themes; adjust existing
  components' appearance, preserving all behavioral selectors and functions.
- [x] Run frontend tests/typecheck/build; browser-check all three pages in both
  themes, 390px and 320px layouts, keyboard controls, reload and download flow.
- [x] Inspect screenshots, fix visual defects and document usage in README.

Review focus: dark input/log contrast, long IDs and code on mobile, theme control
keyboard focus, storage denial, no changes to result truthfulness or API calls.
No new dependencies, backend changes, automatic commit or push.

Verified 2026-10-23: 58 frontend tests; Vue/TypeScript and Vite production build.
Edge: both themes on submission/execution/experience pages, 320/390px layout,
keyboard selection, reload persistence, unchanged form/log state, actual JSON
download and experience paging. No console errors/warnings. Independent review
found no actionable issues. Removed background-color transitions to avoid a brief
low-contrast state when changing themes; dark/light controls update together.
Screenshots and browser check script remain ignored in output/playwright/.
