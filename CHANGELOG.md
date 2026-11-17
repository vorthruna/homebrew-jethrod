# Changelog

All notable changes to jethrod. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [0.2.3] - 2026-11-17

### Added
- End-to-end tests (`tests/test_e2e.py`): the real process against a local fake internet
  (tour feed, WordPress API, ntfy) with stand-in desktop notifiers. `JETHROD_E2E_CMD` runs
  them against an installed copy.
- `site` config key, so the digest can be pointed at another WordPress site.
- `tests/test_docs.py`: every command and config key must be in the README and man page.

### Fixed
- On Linux, a `terminal-notifier` on PATH was chosen over `notify-send`; it failed and the
  desktop alert was lost. `terminal-notifier` is now used only on macOS.
- After a North American date was reported, every later poll still logged "none in North
  America". It now logs "N in North America, already reported".
- The README described 0.1.0 (macOS only, no digest); it now documents the current release.

## [0.2.2] - 2026-11-13

### Fixed
- A CLI `check` running alongside the daemon could alert the same date twice or overwrite
  the site baseline; state updates now take a cross-process lock.
- Bare South American city names at evening shows were flagged as North America.
- Offline, missing-file and bad-JSON errors print one `jethrod: ...` line and exit 1
  instead of a traceback (`JETHROD_DEBUG=1` keeps it).
- The macOS `osascript` fallback showed `\u` escapes instead of Unicode.
- Windows `pythonw` (no stdout) crashed on exit; a state file missing keys crashed alerting.
- `--help` text ended mid-sentence; `config` told Windows users to run `reload`.
- Packaging uses an SPDX license string (the table form stops building after 2027-02-18).

## [0.2.1] - 2026-11-13

### Fixed
- `jethrod status | head` printed a BrokenPipeError traceback; it now exits 141.

## [0.2.0] - 2026-11-13

### Added
- Windows support: toast notifications, Startup-folder service, pipx install.
- Linux daemon conventions: `Type=notify` with watchdog, signals, pidfile lock,
  `--daemon`, journal/syslog/file logging, FHS and systemd paths, LSB exit codes,
  `stop`/`reload`/`kick`, `install-service`; units and init scripts in `contrib/`; man page.
- Daily jethrotull.com digest from the WordPress REST API.
- Pairwise/3-wise tests and a mutation check.

### Fixed
- ISO country codes read as state codes (`Bonn, DE` as Delaware), namesake cities
  (`Rochester, UK`), late European shows read as American, Notepad's BOM in the config,
  `digest_time=24:00` and `interval_minutes=inf`, UTF-8 titles on ntfy, Windows pidfile
  cleanup.

## [0.1.0] - 2026-11-13

### Added
- First release: polls the official tour feed, alerts once per North American date via a
  macOS notification and ntfy; Homebrew formula and `brew services`.

[0.2.3]: https://github.com/vorthruna/homebrew-jethrod/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/vorthruna/homebrew-jethrod/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/vorthruna/homebrew-jethrod/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/vorthruna/homebrew-jethrod/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vorthruna/homebrew-jethrod/releases/tag/v0.1.0
