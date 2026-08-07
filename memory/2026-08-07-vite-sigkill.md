# Vite SIGKILL investigation — 2026-08-07

## Debug report

- **Symptom:** Running `npm --prefix web run dev` returned
  `zsh: killed npm --prefix web run dev`, indicating that the process received
  `SIGKILL` rather than exiting with a Vite or Node error.
- **Root cause:** Transient system memory pressure. At investigation time macOS
  reported 8.3 GB of 9.2 GB swap in use, alongside multiple browser and IDE
  processes consuming hundreds of megabytes each. No Node/Vite crash report was
  present.
- **Fix:** No repository change was required. Free memory by closing unused
  browser tabs or IDEs, then retry the command; restart macOS if it is killed
  again.
- **Evidence:** The same command started Vite 7.3.6 successfully in 211 ms. Its
  Node process used approximately 67 MB RSS, and an HTTP request to
  `http://127.0.0.1:5173/` returned the dashboard HTML.
- **Regression test:** Not applicable because this was an operating-system
  resource termination rather than an application regression.
- **Related:** The installed Node version is 22.12.0. The repository recommends
  Node 22.14 or newer, but the version difference does not explain `SIGKILL` and
  the server runs successfully on the installed version.
- **Status:** DONE_WITH_CONCERNS — the original one-off kill cannot be replayed
  deterministically, but the high swap usage and successful fresh reproduction
  isolate the issue from the application.
