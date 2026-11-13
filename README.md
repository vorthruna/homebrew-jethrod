# jethrod

```
   o==|::::::::::::::::|==o     standing on one leg,
                                listening for the flute.
```

A tiny novelty daemon that watches the official Jethro Tull tour feed and
pings you **the moment Ian Anderson books a show in the US, Canada, or Mexico**.
macOS notification + optional phone push via [ntfy](https://ntfy.sh). No email. Ever.

## Install

```sh
brew tap vorthruna/jethrod
brew install jethrod
brew services start jethrod      # runs at login, checks every 30 min
```

## Phone alerts (optional, free, no account)

1. Install the **ntfy** app (iOS / Android) and subscribe to a topic name only you know.
2. Tell jethrod about it:

```sh
jethrod config ntfy_topic jethrod-<something-unguessable>
brew services restart jethrod
jethrod test-alert
```

## Commands

| command | does |
|---|---|
| `jethrod status` | every booked date; `>>` marks North American ones |
| `jethrod check` | one check now (exit 2 if something new) |
| `jethrod test-alert` | fire a test on every channel |
| `jethrod config [KEY VALUE]` | show/set `interval_minutes`, `ntfy_topic`, `ntfy_server`, `desktop` |
| `jethrod forget` | clear alert history |
| `jethrod selftest` | verify the geography logic |

Logs: `$(brew --prefix)/var/log/jethrod.log`. Config: `~/.config/jethrod/config`.
State: `~/.local/state/jethrod/seen.json`. Each date alerts once; a failed delivery retries next poll.

## How it decides "North America"

The feed (the JSON behind jethrotull.com/tour-dates) only gives a free-text address,
so jethrod checks state/province codes, country names, and a list of NA-only cities.
If the address is just a bare city, it falls back to the showtime: an evening show
that lands at 22:00 UTC or later (or on the next UTC day) is flagged `Americas?`.

Unofficial fan toy; not affiliated with Jethro Tull or Ian Anderson. MIT licensed.
