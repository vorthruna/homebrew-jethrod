# jethrod

```
   o==|::::::::::::::::|==o     standing on one leg,
                                listening for the flute.
```

A small daemon that pings you **the moment Jethro Tull / Ian Anderson book a show in the
US, Canada or Mexico**, plus a **once-a-morning digest** of anything new or changed on
jethrotull.com (news, pages, discography, video, gallery, merch, band).
Desktop notification (macOS, Windows, Linux) and an optional phone push via
[ntfy](https://ntfy.sh). No email. Ever.

| alert | priority | when |
|---|---|---|
| North American tour date | high, with sound | within one poll (30 min by default) |
| site digest | normal | once a day at `digest_time` (08:00), or the first poll after it |

Each date and each site change is reported once. If every channel fails, it is retried on
the next poll.

## Install

**macOS / Linux (Homebrew)**
```sh
brew tap vorthruna/jethrod
brew install jethrod
brew services start jethrod          # starts now and at login
```

**Windows** (Python 3.9+)
```powershell
pipx install git+https://github.com/vorthruna/homebrew-jethrod.git
jethrod install-service              # starts now and at every logon; no admin, no console window
```

**Linux without Homebrew**
```sh
pipx install git+https://github.com/vorthruna/homebrew-jethrod.git
jethrod install-service              # systemd --user unit: Type=notify, watchdog
```

For a headless server, use the hardened system unit in
[`contrib/systemd/jethrod.service`](contrib/systemd/jethrod.service) (DynamicUser,
sandboxed, phone pushes only). Scripts for sysvinit and OpenRC and a logrotate config are in
[`contrib/`](contrib/).

## Phone alerts (free, no account)

Install the **ntfy** app (iOS / Android), subscribe to a topic name only you know, then:
```sh
jethrod config ntfy_topic jethrod-<something-unguessable>
jethrod reload                       # Windows: jethrod install-service
jethrod test-alert
```

Running jethrod on two machines? Give the phone topic to one of them and set
`desktop on`, `ntfy_topic` empty on the other, or you get two pushes per date.

## Commands

| command | does |
|---|---|
| `jethrod status` | daemon state, alert history, every booked date (`>>` marks North America) |
| `jethrod run [-d] [--log [FILE]] [--syslog] [-v]` | the daemon; foreground unless `-d` |
| `jethrod stop` / `reload` / `kick [--site]` | SIGTERM / SIGHUP / SIGUSR1 (`--site`: SIGUSR2) through the pidfile |
| `jethrod install-service` / `uninstall-service` | systemd user unit (Linux), Startup shortcut (Windows) |
| `jethrod check` | check the tour feed once (exit 2 if something new) |
| `jethrod site [--recent]` | run the site digest once / list the 10 latest site changes |
| `jethrod test-alert` | send a test on every channel |
| `jethrod config [KEY VALUE]` | show the config, or set one key |
| `jethrod forget` | clear the alert history and the site baseline |
| `jethrod selftest` | check the address logic |

Global flags: `-c/--config FILE`, `--state-dir DIR`, `--pidfile FILE`.
Full reference: `man jethrod`.

## Configuration

`~/.config/jethrod/config` (Windows: `%APPDATA%\jethrod\config`; root: `/etc/jethrod/config`),
plain `key=value`. Every key can also be set with `JETHROD_<KEY>` in the environment.

| key | default | meaning |
|---|---|---|
| `interval_minutes` | `30` | minutes between tour checks (5 to 1440) |
| `ntfy_topic` | empty (off) | your secret ntfy topic |
| `ntfy_server` | `https://ntfy.sh` | a self-hosted ntfy server, if you run one |
| `desktop` | `on` | desktop notifications |
| `site_watch` | `on` | the daily site digest |
| `digest_time` | `08:00` | local time of the daily digest |
| `feed` | the official tour feed | the Seated JSON behind jethrotull.com/tour-dates |
| `site` | `https://jethrotull.com` | the WordPress site the digest reads |

Errors print one `jethrod: ...` line; set `JETHROD_DEBUG=1` for the full traceback.

## Daemon behavior

- **Signals:** TERM/INT stop cleanly · HUP reloads config and reopens the log · USR1 checks now · USR2 runs the digest now
- **Pidfile is a lock:** a second copy refuses to start (exit 1); a stale file left by a crash never blocks a start
- **One writer at a time:** the daemon and a CLI `check` or `site` share state under a lock, so nothing alerts twice
- **systemd:** `Type=notify` (READY, STATUS, RELOADING, STOPPING), `WatchdogSec` pings, journal priority prefixes, and the `$STATE_DIRECTORY`, `$CONFIGURATION_DIRECTORY`, `$RUNTIME_DIRECTORY` and `$LOGS_DIRECTORY` paths
- **sysvinit:** `run --daemon` double-forks, calls `setsid`, `chdir /`, `umask 022` and sends stdio to `/dev/null`
- **Logging:** stdout, the journal, `--syslog` (facility daemon), or `--log FILE` (reopened after logrotate)
- **Paths:** XDG on Linux and macOS, FHS (`/etc/jethrod`, `/var/lib/jethrod`) as root, `%APPDATA%` / `%LOCALAPPDATA%` on Windows
- **Exit codes:** 0 ok · 1 error or already running · 2 new dates (`check`) · 3 not running (LSB) · 141 output pipe closed

## How it decides "North America"

The tour feed only has a free-text address, so jethrod reads it from the end:

1. A country, state or province name or code decides: `Austin, TX`, `Toronto, ON M5V 3L9`, `Mexico City, CDMX`, `Bonn, Germany`.
2. Codes that are also country codes (`DE` Germany or Delaware, `NL`, `SK`, `AR`, `PA`, ...) and city names that also exist abroad (`Rochester`, `Halifax`, `Portland`, ...) count only if the showtime agrees.
3. A bare city name with nothing else falls back to the showtime. An evening show that starts at 22:00 UTC or later, or on the next UTC day, is flagged `Americas?`. Known South American cities are excluded.

The site digest reads WordPress's REST API, which knows each item's real modified time
(the sitemap's `lastmod` lags by months). The first run records a silent baseline, section by
section.

## Development

```sh
python -m unittest discover -s tests              # everything below
python tests/mutate.py                            # re-inject known bugs; each must be caught
JETHROD_E2E_CMD=$(command -v jethrod) \
  python -m unittest discover -s tests -p test_e2e.py   # e2e against an installed copy
```

| suite | what it covers |
|---|---|
| `test_jethrod.py` | units, the pidfile lock, sd_notify, a full `--daemon` lifecycle |
| `test_pairwise.py` | pairwise and 3-wise covering arrays for each place where inputs interact: address placement, alert-once, site digest, config precedence, schedule, notification routing |
| `test_bugbash.py` | one regression test per bug found in the bug bash |
| `test_e2e.py` | the real process against a local fake internet (tour feed, WordPress, ntfy) with stand-in desktop notifiers: alerts with traps, outages and retries, digests, and the daemon story (signals, reload, restart) |
| `test_docs.py` | every command and config key is in this README and the man page |
| `mutate.py` | re-injects each known bug into a copy; the suite must catch every one |

CI runs all of it on Linux, macOS and Windows with Python 3.9 and 3.13, runs the system unit
under real systemd, and builds the sdist with deprecation warnings as errors. Changes are
listed in [CHANGELOG.md](CHANGELOG.md).

Unofficial fan toy; not affiliated with Jethro Tull or Ian Anderson. MIT licensed.
