#!/usr/bin/env python3
"""jethrod - a tiny daemon that stands on one leg and listens for
Jethro Tull / Ian Anderson shows in North America.

Polls the official tour feed behind jethrotull.com/tour-dates, remembers
what it has already told you about, and alerts via a desktop notification
(macOS, Windows, Linux) and optionally an ntfy.sh push to your phone.
Every morning it also sends one digest of anything new or changed on
jethrotull.com (news, pages, discography, video...). No email. Ever.
"""
import argparse
import base64
import datetime as dt
import html
import json
import logging
import logging.handlers
import math
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

VERSION = "0.2.0"
WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if WINDOWS else 0  # CREATE_NO_WINDOW
PS_APPID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
FEED = ("https://cdn.seated.com/api/tour/"
        "37997a6d-2133-40b4-90dd-e58363cc2192?include=tour-events")
TOUR_PAGE = "https://jethrotull.com/tour-dates/"
UA = "jethrod/%s (+https://github.com/vorthruna/homebrew-jethrod)" % VERSION

BANNER = r"""
        _   _                   _
       (_) | |                 | |      o==|::::::::::::::::|==o
        _  | |_  ___ _ __  _ __| |
       | | | __|/ _ \ '_ \| '__| |      jethrod %s
       | | | |_|  __/ | | | |  |_|      standing on one leg,
       |_|  \__|\___|_| |_|_|  (_)      listening for the flute.
      _/ |
     |__/
""" % VERSION

def app_dirs(osname=os.name, env=os.environ, euid=None):
    """(config_dir, state_dir), most specific first:
    systemd ConfigurationDirectory=/StateDirectory=, then XDG, then the
    platform default - FHS (/etc, /var/lib) when running as root on POSIX,
    %APPDATA%/%LOCALAPPDATA% on Windows, ~/.config / ~/.local/state otherwise."""
    home = os.path.expanduser("~")
    if euid is None:
        euid = os.geteuid() if hasattr(os, "geteuid") else -1
    j = os.path.join
    if osname == "nt":
        cfg = env.get("XDG_CONFIG_HOME") or env.get("APPDATA") or j(home, "AppData", "Roaming")
        st = env.get("XDG_STATE_HOME") or env.get("LOCALAPPDATA") or j(home, "AppData", "Local")
        return j(cfg, "jethrod"), j(st, "jethrod")
    sd_cfg = (env.get("CONFIGURATION_DIRECTORY") or "").split(":")[0]
    sd_st = (env.get("STATE_DIRECTORY") or "").split(":")[0]
    if sd_cfg or sd_st:  # systemd already appended "jethrod"
        return sd_cfg or "/etc/jethrod", sd_st or "/var/lib/jethrod"
    if euid == 0 and not env.get("XDG_CONFIG_HOME") and not env.get("XDG_STATE_HOME"):
        return "/etc/jethrod", "/var/lib/jethrod"
    cfg = env.get("XDG_CONFIG_HOME") or j(home, ".config")
    st = env.get("XDG_STATE_HOME") or j(home, ".local", "state")
    return j(cfg, "jethrod"), j(st, "jethrod")


SYSTEM_CONFIG = "/etc/jethrod/config"


def set_paths(config=None, state_dir=None, pidfile=None, env=os.environ):
    """Resolve every path once; CLI flags > JETHROD_* env > app_dirs()."""
    global CONFIG_DIR, STATE_DIR, CONFIG_FILE, STATE_FILE, LOG_FILE, PID_FILE
    cfg_dir, st_dir = app_dirs(env=env)
    CONFIG_FILE = config or env.get("JETHROD_CONFIG") or os.path.join(cfg_dir, "config")
    CONFIG_DIR = os.path.dirname(CONFIG_FILE)
    STATE_DIR = state_dir or env.get("JETHROD_STATE_DIR") or st_dir
    STATE_FILE = os.path.join(STATE_DIR, "seen.json")
    LOG_FILE = os.path.join((env.get("LOGS_DIRECTORY") or "").split(":")[0] or STATE_DIR,
                            "jethrod.log")
    runtime = (env.get("RUNTIME_DIRECTORY") or "").split(":")[0]
    PID_FILE = (pidfile or env.get("JETHROD_PIDFILE")
                or (os.path.join(runtime, "jethrod.pid") if runtime else None)
                or os.path.join(STATE_DIR, "jethrod.pid"))


set_paths()
PID_FILE = os.path.join(STATE_DIR, "jethrod.pid")

DEFAULTS = {
    "interval_minutes": "30",
    "ntfy_topic": "",
    "ntfy_server": "https://ntfy.sh",
    "desktop": "on",
    "feed": FEED,
    "site_watch": "on",
    "digest_time": "08:00",
}

SITE = "https://jethrotull.com"
# WordPress REST bases on jethrotull.com, in digest order.
SITE_TYPES = [("posts", "News"), ("pages", "Page"), ("discography-item", "Discography"),
              ("the-attic", "The Attic"), ("video-content", "Video"),
              ("gallery", "Gallery"), ("merch", "Merch"), ("band-member", "Band")]

# --------------------------------------------------------------- geography
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}
CA_PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia", "ON": "Ontario", "PE": "Prince Edward Island",
    "QC": "Quebec", "SK": "Saskatchewan", "NT": "Northwest Territories",
    "NU": "Nunavut", "YT": "Yukon",
}
# US/CA codes that are also ISO 3166 country codes. "Bonn, DE" is Germany,
# not Delaware; "Utrecht, NL" is not Newfoundland. These need a second signal.
COLLIDING_CODES = {"AL", "AR", "AZ", "CO", "DE", "GA", "ID", "IL", "IN", "KY", "LA",
                   "MA", "MD", "ME", "MN", "MO", "MS", "MT", "NC", "NE", "PA", "SC",
                   "SD", "TN", "VA", "NL", "PE", "SK", "NU"}
NA_COUNTRY = {
    "usa": "US", "u.s.a": "US", "u.s": "US", "us": "US", "united states": "US",
    "united states of america": "US", "america": "US",
    "canada": "CA",
    "mexico": "MX", "méxico": "MX", "mx": "MX", "cdmx": "MX", "ciudad de méxico": "MX",
    "jalisco": "MX", "nuevo león": "MX", "nuevo leon": "MX", "baja california": "MX",
    "quintana roo": "MX", "estado de méxico": "MX",
}
FOREIGN = {
    "uk", "gb", "u.k", "united kingdom", "england", "scotland", "wales", "northern ireland",
    "ireland", "ie", "germany", "deutschland", "at", "austria", "österreich", "ch",
    "switzerland", "schweiz", "suisse", "it", "italy", "italia", "es", "spain", "españa",
    "pt", "portugal", "fr", "france", "be", "belgium", "belgique", "netherlands",
    "the netherlands", "holland", "nederland", "lu", "luxembourg", "dk", "denmark", "se",
    "sweden", "no", "norway", "fi", "finland", "is", "iceland", "pl", "poland", "polska",
    "cz", "czech republic", "czechia", "slovakia", "hu", "hungary", "si", "slovenia", "hr",
    "croatia", "rs", "serbia", "ro", "romania", "bg", "bulgaria", "gr", "greece", "tr",
    "turkey", "türkiye", "israel", "ru", "russia", "ua", "ukraine", "ee", "estonia", "lv",
    "latvia", "lt", "lithuania", "malta", "cy", "cyprus", "au", "australia", "nz",
    "new zealand", "jp", "japan", "br", "brazil", "argentina", "cl", "chile", "peru",
    "colombia", "uy", "uruguay", "za", "south africa",
}
STATE_NAMES = {n.lower(): ("US", n == "Georgia") for n in US_STATES.values()}
STATE_NAMES.update({n.lower(): ("CA", False) for n in CA_PROVINCES.values()})
STATE_NAMES["québec"] = ("CA", False)
# Cities that, bare, mean North America...
NA_CITIES = {
    "US": ["new york", "new york city", "nyc", "brooklyn", "chicago", "los angeles",
           "san francisco", "seattle", "denver", "phoenix", "philadelphia", "pittsburgh",
           "detroit", "cleveland", "atlanta", "nashville", "austin", "dallas", "houston",
           "san antonio", "minneapolis", "milwaukee", "st. louis", "kansas city",
           "las vegas", "salt lake city", "san diego", "sacramento", "baltimore", "miami",
           "orlando", "tampa", "charlotte", "raleigh", "indianapolis", "cincinnati",
           "buffalo", "saratoga springs", "new orleans", "memphis", "albuquerque", "tucson",
           "oakland", "santa rosa", "morrison", "red rocks", "atlantic city",
           # ...and these mostly do, but a namesake abroad is real:
           "rochester", "albany", "portland", "boston", "berkeley", "hollywood", "columbus"],
    "CA": ["toronto", "montreal", "montréal", "vancouver", "calgary", "edmonton", "ottawa",
           "winnipeg", "quebec city", "québec", "halifax", "laval", "hamilton", "kingston"],
    "MX": ["mexico city", "guadalajara", "monterrey", "tijuana", "puebla"],
}
# Rochester & Halifax (UK), Laval (France), Albany (Australia), Portland (Dorset)...
DUP_CITIES = {"rochester", "albany", "portland", "boston", "berkeley", "hollywood",
              "columbus", "halifax", "laval", "hamilton", "kingston"}
NA_CITY = {c: r for r, cities in NA_CITIES.items() for c in cities}
REGIONS = {"US", "CA", "MX"}


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower()).rstrip(".")


def locate(address):
    """(region, confidence) for a free-text address.
    confidence: "sure"    - North American, no doubt
                "maybe"   - North American only if the showtime agrees
                "foreign" - names a country outside North America
                "unknown" - says nothing useful"""
    parts = [_norm(p) for p in (address or "").split(",")]
    city, quals = parts[0], [q for q in parts[1:] if q]
    if not city and not quals:
        return None, "unknown"
    city_region = NA_CITY.get(city)
    for q in reversed(quals):  # most significant qualifier is usually last
        if q in NA_COUNTRY:
            return NA_COUNTRY[q], "sure"
        if q in FOREIGN:
            return None, "foreign"
        if q in STATE_NAMES:
            reg, colliding = STATE_NAMES[q]
            return reg, ("maybe" if colliding and city_region != reg else "sure")
        code = q.split(" ", 1)[0].upper()  # "NY 10019", "ON M5V 3L9"
        if len(code) == 2 and code.isalpha():
            reg = "US" if code in US_STATES else "CA" if code in CA_PROVINCES else None
            if reg:
                if code == "CA" and city_region:
                    reg = city_region  # "Toronto, CA": Canada, not California
                if code in COLLIDING_CODES and city_region != reg:
                    return reg, "maybe"
                return reg, "sure"
    if city_region:
        return city_region, ("maybe" if city in DUP_CITIES else "sure")
    return None, "unknown"


def classify(address):
    """Region if the address alone points at North America (sure or maybe)."""
    reg, conf = locate(address)
    return reg if conf in ("sure", "maybe") else None


def clock_verdict(attrs):
    """'americas' | 'other' | 'unknown', from when the show starts in UTC.
    Evening shows west of UTC land at 22:00Z or later, or on the next UTC day;
    Europe's evening is 17:00-21:00Z."""
    if not attrs.get("is-starts-at-known"):
        return "unknown"
    try:
        raw = str(attrs["starts-at"]).strip().replace("Z", "+00:00")
        when = dt.datetime.fromisoformat(raw)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        utc = when.astimezone(dt.timezone.utc)
        local = dt.date.fromisoformat(str(attrs["starts-at-date-local"])[:10])
    except (KeyError, ValueError, TypeError):
        return "unknown"
    if utc.date() != local:
        return "americas" if utc.date() > local else "other"
    return "americas" if utc.hour >= 22 else "other"


def looks_american_by_clock(attrs):
    return clock_verdict(attrs) == "americas"


# ------------------------------------------------------------ config/state
def flag(value, default=True):
    """on/off parsing that forgives case and stray whitespace."""
    v = str(value if value is not None else "").strip().lower()
    if v in ("off", "0", "false", "no", "n"):
        return False
    if v in ("on", "1", "true", "yes", "y"):
        return True
    return default


def _config_value(raw):
    v = raw.strip()
    m = re.search(r"\s#", v)  # inline "  # comment" (ntfy topics can't contain '#')
    return v[:m.start()].rstrip() if m else v


def load_config():
    cfg = dict(DEFAULTS)
    files = [CONFIG_FILE]
    if os.name != "nt" and os.path.abspath(CONFIG_FILE) != SYSTEM_CONFIG:
        files.insert(0, SYSTEM_CONFIG)
    for path in files:
        try:
            with open(path, encoding="utf-8-sig") as fh:  # -sig: Notepad's BOM
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        cfg[k.strip()] = _config_value(v)
        except OSError:
            pass
    for k in DEFAULTS:
        env = os.environ.get("JETHROD_" + k.upper())
        if env is not None:
            cfg[k] = env
    return cfg


def save_config_value(key, value):
    if key not in DEFAULTS:
        raise SystemExit("unknown key %r (known: %s)" % (key, ", ".join(DEFAULTS)))
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    lines, found = [], False
    try:
        with open(CONFIG_FILE, encoding="utf-8-sig") as fh:
            for line in fh:
                if line.split("=", 1)[0].strip() == key:
                    lines.append("%s=%s\n" % (key, value))
                    found = True
                else:
                    lines.append(line)
    except FileNotFoundError:
        pass
    if not found:
        lines.append("%s=%s\n" % (key, value))
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {"alerted": {}, "last_check": None, "last_total": None}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)


# ------------------------------------------------------------------- feed
def fetch(url):
    if url.startswith("file://"):
        with open(url[7:], encoding="utf-8") as fh:
            return json.load(fh)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/vnd.api+json, application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def events(doc):
    out = []
    for inc in doc.get("included", []):
        if inc.get("type") != "tour-events":
            continue
        a = inc.get("attributes", {})
        region, conf = locate(a.get("formatted-address"))
        verdict = clock_verdict(a)
        if conf == "maybe" and verdict == "other":
            region = None  # "Bonn, DE" at 19:00 CET is Germany
        out.append({
            "id": inc.get("id"),
            "date": a.get("starts-at-date-local") or "?",
            "venue": a.get("venue-name") or "?",
            "where": a.get("formatted-address") or "?",
            "sold_out": bool(a.get("is-sold-out")),
            "region": region,
            "tentative": region is not None and conf == "maybe",
            "clock_hint": conf == "unknown" and verdict == "americas",
        })
    out.sort(key=lambda e: e["date"])
    return out


def interesting(evts):
    return [e for e in evts if e["region"] in REGIONS or e["clock_hint"]]


def describe(e):
    tag = (e["region"] + ("?" if e.get("tentative") else "")) if e["region"] else \
        ("Americas? (by showtime)" if e["clock_hint"] else "")
    return "%s  %s - %s%s%s" % (e["date"], e["where"], e["venue"],
                                " [%s]" % tag if tag else "",
                                "  SOLD OUT" if e["sold_out"] else "")


# ---------------------------------------------------------------- windows
def ps_quote(s):
    """Quote for a PowerShell single-quoted string."""
    return "'" + str(s).replace("'", "''") + "'"


def powershell(script, timeout=60):
    """Run a script in Windows PowerShell with no window and no quoting games."""
    enc = base64.b64encode(("$ErrorActionPreference='Stop';" + script)
                           .encode("utf-16-le")).decode("ascii")
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                           "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                          check=True, capture_output=True, timeout=timeout,
                          creationflags=NO_WINDOW)


def windows_toast_script(title, body, click=TOUR_PAGE):
    """A native Windows 10/11 toast; clicking it opens the tour page."""
    lines = [l for l in body.splitlines() if l.strip()]
    text = " | ".join(lines[:4]) + (" (+%d more)" % (len(lines) - 4) if len(lines) > 4 else "")
    xml = ('<toast activationType="protocol" launch="{url}">'
           '<visual><binding template="ToastGeneric">'
           '<text>{title}</text><text>{text}</text>'
           '</binding></visual>'
           '<audio src="ms-winsoundevent:Notification.Default"/></toast>').format(
        url=html.escape(click), title=html.escape(title), text=html.escape(text))
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml(%s);"
        "$t = [Windows.UI.Notifications.ToastNotification]::new($x);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(%s).Show($t)"
    ) % (ps_quote(xml), ps_quote(PS_APPID))


def startup_shortcut():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup", "jethrod.lnk")


def daemon_command():
    """Windowless interpreter + this file, so the daemon has no console."""
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return [pyw if os.path.exists(pyw) else exe, os.path.abspath(__file__),
            "run", "--log", LOG_FILE]


# ------------------------------------------------------------ pidfile/lock
try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None

_LOCK_OFFSET = 1 << 20  # Windows locks bytes; lock one far past the pid text


class AlreadyRunning(Exception):
    pass


class PidFile:
    """A pidfile that is also an exclusive lock, so a second daemon refuses to
    start and a stale file left by a crash never blocks the next start."""

    def __init__(self, path):
        self.path, self.fd = path, None

    @staticmethod
    def lock(fd):
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt:
            os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            os.lseek(fd, 0, os.SEEK_SET)

    def acquire(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            self.lock(fd)
        except OSError:
            os.close(fd)
            raise AlreadyRunning(read_pid()) from None
        os.ftruncate(fd, 0)
        os.write(fd, ("%d\n" % os.getpid()).encode())
        os.fsync(fd)
        self.fd = fd
        return self

    def release(self):
        if self.fd is None:
            return
        # POSIX: unlink while still locked, so we can't delete a successor's file.
        # Windows: an open file can't be deleted, so close first.
        if WINDOWS:
            os.close(self.fd)
        try:
            os.unlink(self.path)
        except OSError:
            pass
        if not WINDOWS:
            os.close(self.fd)
        self.fd = None


def read_pid():
    try:
        with open(PID_FILE) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def running_pid():
    """pid of the live daemon, or None. Asks the lock, not the (maybe stale) file."""
    try:
        fd = os.open(PID_FILE, os.O_RDWR)
    except OSError:
        return None
    try:
        PidFile.lock(fd)
    except OSError:
        return read_pid()
    finally:
        os.close(fd)  # closing drops our probe lock
    return None


def signal_daemon(sig):
    pid = running_pid()
    if not pid:
        return None
    os.kill(pid, sig)
    return pid


def stop_daemon(wait=10.0):
    pid = signal_daemon(signal.SIGTERM)  # TerminateProcess on Windows
    end = time.monotonic() + wait
    while pid and running_pid() and time.monotonic() < end:
        time.sleep(0.2)
    return pid


# ------------------------------------------------------- service install
SYSTEMD_USER_UNIT = os.path.join(os.environ.get("XDG_CONFIG_HOME") or
                                 os.path.expanduser("~/.config"),
                                 "systemd", "user", "jethrod.service")


def systemd_user_unit(exec_start):
    return """[Unit]
Description=jethrod - listens for Jethro Tull North American tour dates
Documentation=man:jethrod(1) https://github.com/vorthruna/homebrew-jethrod
Wants=network-online.target
After=network-online.target

[Service]
Type=notify
NotifyAccess=main
ExecStart=%s run
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=30
WatchdogSec=10min

[Install]
WantedBy=default.target
""" % exec_start


def install_service():
    if sys.platform == "darwin":
        print("On macOS use Homebrew:  brew services start jethrod")
        return 1
    if not WINDOWS:
        if not shutil.which("systemctl"):
            print("No systemd here. Init scripts for sysvinit/OpenRC live in contrib/ (see man jethrod).")
            return 1
        exec_start = " ".join('"%s"' % a if " " in a else a
                              for a in (sys.executable, os.path.abspath(__file__)))
        os.makedirs(os.path.dirname(SYSTEMD_USER_UNIT), exist_ok=True)
        with open(SYSTEMD_USER_UNIT, "w") as fh:
            fh.write(systemd_user_unit(exec_start))
        for cmd in (["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", "--now", "jethrod.service"]):
            subprocess.run(cmd, check=True)
        print("installed %s and started it.\n  logs:   journalctl --user -u jethrod -f"
              "\n  reload: systemctl --user reload jethrod" % SYSTEMD_USER_UNIT)
        print("tip: `loginctl enable-linger $USER` keeps it running while logged out.")
        return 0
    os.makedirs(STATE_DIR, exist_ok=True)
    cmd = daemon_command()
    args = subprocess.list2cmdline(cmd[1:])
    powershell(
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(%s);"
        "$s.TargetPath = %s; $s.Arguments = %s; $s.WorkingDirectory = %s;"
        "$s.WindowStyle = 7; $s.Description = 'jethrod - listening for the flute'; $s.Save()"
        % (ps_quote(startup_shortcut()), ps_quote(cmd[0]), ps_quote(args), ps_quote(STATE_DIR)))
    stop_daemon()
    subprocess.Popen(cmd, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    print("jethrod is listening now and will start at every logon.")
    print("  shortcut: %s\n  log:      %s" % (startup_shortcut(), LOG_FILE))
    return 0


def uninstall_service():
    if sys.platform == "darwin":
        print("On macOS use Homebrew:  brew services stop jethrod")
        return 1
    if not WINDOWS:
        if os.path.exists(SYSTEMD_USER_UNIT):
            subprocess.run(["systemctl", "--user", "disable", "--now", "jethrod.service"])
            os.remove(SYSTEMD_USER_UNIT)
            subprocess.run(["systemctl", "--user", "daemon-reload"])
            print("removed %s" % SYSTEMD_USER_UNIT)
        else:
            print("no user unit installed")
        return 0
    try:
        os.remove(startup_shortcut())
    except OSError:
        pass
    pid = stop_daemon()
    print("jethrod removed from startup%s." % (" and stopped (pid %d)" % pid if pid else ""))
    return 0


# ---------------------------------------------------------------- alerts
def notify(cfg, title, body, log=print, click=TOUR_PAGE, priority="high", tags="flute,tada"):
    sent = []
    if flag(cfg.get("desktop", "on")):
        tn = shutil.which("terminal-notifier") or (
            "/opt/homebrew/bin/terminal-notifier"
            if os.path.exists("/opt/homebrew/bin/terminal-notifier") else None)
        try:
            if tn:
                subprocess.run([tn, "-title", title, "-message", body,
                                "-open", click, "-sound", "Glass" if priority == "high" else "Pop",
                                "-group", "jethrod-" + priority], check=True,
                               capture_output=True, timeout=15)
                sent.append("terminal-notifier")
            elif sys.platform == "darwin":
                script = "display notification %s with title %s sound name \"Glass\"" % (
                    json.dumps(body), json.dumps(title))
                subprocess.run(["osascript", "-e", script], check=True,
                               capture_output=True, timeout=15)
                sent.append("osascript")
            elif WINDOWS:
                powershell(windows_toast_script(title, body, click), timeout=30)
                sent.append("windows-toast")
            elif shutil.which("notify-send"):
                subprocess.run(["notify-send", title, body], check=True,
                               capture_output=True, timeout=15)
                sent.append("notify-send")
        except (OSError, subprocess.SubprocessError) as exc:
            log("desktop notification failed: %s" % exc)
    topic = cfg.get("ntfy_topic", "").strip()
    if topic:
        # JSON publishing: UTF-8 everywhere (HTTP headers would mangle "Poznań").
        payload = {"topic": topic, "title": title, "message": body, "click": click,
                   "tags": [t for t in tags.split(",") if t],
                   "priority": {"high": 4, "default": 3}.get(priority, 3)}
        req = urllib.request.Request(
            cfg.get("ntfy_server", DEFAULTS["ntfy_server"]).strip().rstrip("/") or
            DEFAULTS["ntfy_server"], data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": UA})
        try:
            urllib.request.urlopen(req, timeout=20).read()
            sent.append("ntfy")
        except Exception as exc:  # noqa: BLE001 - never die on a push
            log("ntfy push failed: %s" % exc)
    return sent


# ------------------------------------------------------------------ core
def check_once(cfg, log=print, quiet=False):
    doc = fetch(cfg["feed"])
    evts = events(doc)
    state = load_state()
    fresh = [e for e in interesting(evts) if e["id"] not in state["alerted"]]
    state["last_check"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    state["last_total"] = len(evts)
    if fresh:
        title = "Jethro Tull in North America!" if len(fresh) == 1 else \
            "Jethro Tull: %d North American dates!" % len(fresh)
        body = "\n".join("%s %s - %s" % (e["date"], e["where"], e["venue"]) for e in fresh)
        channels = notify(cfg, title, body, log=log)
        for e in fresh:
            log("NEW  " + describe(e))
        log("alerted via: %s" % (", ".join(channels) or "nothing (no channel worked!)"))
        if channels:
            for e in fresh:
                state["alerted"][e["id"]] = describe(e)
    elif not quiet:
        log("%d dates on the books, none in North America. Still on one leg." % len(evts))
    save_state(state)
    return fresh, evts


# ------------------------------------------------------------ site digest
def _plain(rendered):
    return html.unescape(re.sub(r"<[^>]+>", "", rendered or "")).strip() or "(untitled)"


def site_snapshot(get=None, log=print, ok=None):
    """Most-recently-modified 50 items of every content type on the site,
    straight from WordPress (more truthful than its sitemap's lastmod).
    Bases that answered are appended to `ok`."""
    get = get or fetch
    ok = ok if ok is not None else []
    snap, failures = {}, 0
    for base, label in SITE_TYPES:
        url = ("%s/wp-json/wp/v2/%s?per_page=50&orderby=modified&order=desc"
               "&_fields=id,link,modified_gmt,title" % (SITE, base))
        try:
            items = get(url)
        except Exception as exc:  # noqa: BLE001 - one bad type shouldn't sink the digest
            failures += 1
            log("site: %s unavailable (%s)" % (label, exc))
            continue
        if not isinstance(items, list):
            failures += 1
            log("site: %s answered nonsense" % label)
            continue
        ok.append(base)
        for it in items:
            snap["%s:%s" % (base, it.get("id"))] = {
                "label": label, "title": _plain((it.get("title") or {}).get("rendered")),
                "link": it.get("link") or SITE, "modified": it.get("modified_gmt") or ""}
    if failures == len(SITE_TYPES):
        raise RuntimeError("site unreachable")
    return snap


def site_changes(known, snap):
    """[(kind, item)] for items new to us or modified since we last looked."""
    out = []
    order = {label: i for i, (_, label) in enumerate(SITE_TYPES)}
    for key, item in snap.items():
        if key not in known:
            out.append(("new", item))
        elif item["modified"] > known[key]:
            out.append(("updated", item))
    out.sort(key=lambda c: (order.get(c[1]["label"], 99), c[1]["modified"]), reverse=False)
    return out


def site_digest(cfg, log=print, get=None, notify_fn=None):
    """Compare the site against what we saw last time; one notification for all of it.
    First run just records a baseline."""
    notify_fn = notify_fn or notify
    state = load_state()
    ok = []
    snap = site_snapshot(get=get, log=log, ok=ok)
    known = state.get("site") or {}
    seen_types = set(state.get("site_types") or {k.split(":", 1)[0] for k in known})
    first = not known
    # A section seen for the first time (e.g. it was down during the baseline)
    # is baselined silently instead of reporting all 50 of its items as new.
    fresh_types = set(ok) - seen_types
    changes = [] if first else site_changes(
        known, {k: v for k, v in snap.items() if k.split(":", 1)[0] not in fresh_types})
    if changes:
        n = len(changes)
        title = "jethrotull.com: %d update%s" % (n, "" if n == 1 else "s")
        lines = ["%s%s: %s" % ("New " if k == "new" else "", it["label"], it["title"])
                 for k, it in changes[:6]]
        if n > 6:
            lines.append("+%d more" % (n - 6))
        click = changes[0][1]["link"] if n == 1 else SITE
        sent = notify_fn(cfg, title, "\n".join(lines), log=log, click=click,
                         priority="default", tags="newspaper")
        for k, it in changes:
            log("site %-7s %s: %s  %s" % (k, it["label"], it["title"], it["link"]))
        if not sent:
            log("site digest not delivered; will retry next poll")
            return changes
    elif first:
        log("site: baseline of %d items recorded; changes will be reported from now on" % len(snap))
    else:
        log("site: no changes since last look")
    known.update({k: v["modified"] for k, v in snap.items()})
    state["site"] = known
    state["site_types"] = sorted(seen_types | set(ok))
    state["site_last_run"] = dt.date.today().isoformat()
    save_state(state)
    return changes


def digest_hm(cfg):
    """(hour, minute) of the daily digest; anything invalid means 08:00."""
    try:
        hh, mm = [int(x) for x in str(cfg.get("digest_time", "08:00")).strip().split(":", 1)]
    except ValueError:
        return 8, 0
    return (hh, mm) if 0 <= hh <= 23 and 0 <= mm <= 59 else (8, 0)


def site_due(cfg, state, now=None):
    if not flag(cfg.get("site_watch", "on")):
        return False
    now = now or dt.datetime.now()
    hh, mm = digest_hm(cfg)
    return state.get("site_last_run") != now.date().isoformat() and \
        (now.hour, now.minute) >= (hh, mm)


# ----------------------------------------------------------------- daemon
LOG = logging.getLogger("jethrod")


class _JournalFormatter(logging.Formatter):
    """sd-daemon(3) priority prefixes; journald adds its own timestamps."""
    PRI = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4,
           logging.ERROR: 3, logging.CRITICAL: 2}

    def format(self, record):
        return "<%d>%s" % (self.PRI.get(record.levelno, 6), record.getMessage())


def setup_logging(log_file=None, use_syslog=False, verbose=False):
    for h in list(LOG.handlers):
        LOG.removeHandler(h)
        h.close()
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)
    LOG.propagate = False
    if use_syslog:
        addr = next((a for a in ("/dev/log", "/var/run/syslog") if os.path.exists(a)),
                    ("localhost", 514))
        h = logging.handlers.SysLogHandler(address=addr,
                                           facility=logging.handlers.SysLogHandler.LOG_DAEMON)
        h.setFormatter(logging.Formatter("jethrod[%(process)d]: %(message)s"))
    elif log_file or sys.stdout is None:  # pythonw has no stdout at all
        path = log_file or LOG_FILE
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # WatchedFileHandler reopens by itself when logrotate moves the file
        h = logging.handlers.WatchedFileHandler(path, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                                         "%Y-%m-%d %H:%M:%S"))
    elif os.environ.get("JOURNAL_STREAM"):
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(_JournalFormatter())
    else:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    LOG.addHandler(h)
    return h


def sd_notify(message, env=os.environ):
    """sd_notify(3) without libsystemd. No-op outside systemd."""
    addr = env.get("NOTIFY_SOCKET")
    if not addr or not hasattr(socket, "AF_UNIX"):
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode("utf-8"))
        return True
    except OSError:
        return False


def watchdog_interval(env=os.environ):
    """Seconds between WATCHDOG=1 pings (half of WatchdogSec), or None."""
    try:
        usec = int(env.get("WATCHDOG_USEC", ""))
    except ValueError:
        return None
    pid = env.get("WATCHDOG_PID")
    if pid and pid != str(os.getpid()):
        return None
    return usec / 2e6 if usec > 0 else None


def daemonize():
    """Classic SysV double fork: detach from the terminal and the session."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    os.chdir("/")
    os.umask(0o022)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    if devnull > 2:
        os.close(devnull)


class Daemon:
    """Signals:  TERM/INT stop cleanly   HUP reload config + reopen log
                 USR1 check the tour feed now   USR2 run the site digest now"""

    def __init__(self, log_file=None, use_syslog=False, verbose=False):
        self.log_args = (log_file, use_syslog, verbose)
        self.stop = self.reload = self.wake = self.force_site = False

    def _on(self, signame, fn):
        sig = getattr(signal, signame, None)
        if sig is not None:
            try:
                signal.signal(sig, lambda *_: fn())
            except (OSError, ValueError):
                pass

    def _install_signals(self):
        def stop():
            self.stop = True

        def reload():
            self.reload = self.wake = True

        def kick():
            self.wake = True

        def site():
            self.force_site = self.wake = True

        for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
            self._on(name, stop)
        self._on("SIGHUP", reload)
        self._on("SIGUSR1", kick)
        self._on("SIGUSR2", site)

    @staticmethod
    def interval(cfg):
        """Seconds between tour checks: 5 min .. 1 day; nonsense means 30 min."""
        try:
            minutes = float(cfg["interval_minutes"])
        except (KeyError, ValueError, TypeError):
            minutes = 30.0
        if not math.isfinite(minutes):
            minutes = 30.0
        return min(max(5.0, minutes), 1440.0) * 60

    def one_round(self, cfg):
        try:
            fresh, evts = check_once(cfg, log=LOG.info)
            status = "%d dates booked, %d new in North America" % (len(evts), len(fresh))
        except Exception as exc:  # noqa: BLE001 - a daemon keeps breathing
            LOG.warning("tour check failed: %s", exc)
            status = "tour check failing: %s" % exc
        try:
            if self.force_site or site_due(cfg, load_state()):
                site_digest(cfg, log=LOG.info)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("site digest failed: %s", exc)
        self.force_site = False
        sd_notify("STATUS=%s" % status)

    def run(self):
        setup_logging(*self.log_args)
        pid = PidFile(PID_FILE)
        try:
            pid.acquire()
        except AlreadyRunning as exc:
            LOG.error("already running (pid %s, %s)", exc.args[0] or "?", PID_FILE)
            return 1
        self._install_signals()
        cfg = load_config()
        wd = watchdog_interval()
        LOG.info("jethrod %s up (pid %d). tour feed every %g min; site digest %s; ntfy %s",
                 VERSION, os.getpid(), self.interval(cfg) / 60,
                 "daily at %02d:%02d" % digest_hm(cfg) if flag(cfg.get("site_watch")) else "off",
                 "on" if cfg.get("ntfy_topic") else "off")
        LOG.debug("config=%s state=%s pidfile=%s watchdog=%s",
                  CONFIG_FILE, STATE_DIR, PID_FILE, wd)
        sd_notify("READY=1\nMAINPID=%d\nSTATUS=starting first check" % os.getpid())
        last_ping = time.monotonic()
        try:
            while not self.stop:
                if self.reload:
                    self.reload = False
                    sd_notify("RELOADING=1")
                    setup_logging(*self.log_args)
                    cfg = load_config()
                    LOG.info("reloaded %s", CONFIG_FILE)
                    sd_notify("READY=1")
                self.wake = False
                self.one_round(cfg)
                deadline = time.monotonic() + self.interval(cfg) * random.uniform(0.9, 1.1)
                while not (self.stop or self.wake) and time.monotonic() < deadline:
                    time.sleep(1.0)  # handlers only set flags; never block long
                    if wd and time.monotonic() - last_ping >= wd:
                        sd_notify("WATCHDOG=1")
                        last_ping = time.monotonic()
        finally:
            sd_notify("STOPPING=1")
            LOG.info("stopping. Too old to rock 'n' roll, too young to die.")
            pid.release()
        return 0


def selftest():
    cases = {  # address: (region, confidence)
        "Boston, MA": ("US", "sure"), "New York, NY 10019": ("US", "sure"),
        "Toronto, ON": ("CA", "sure"), "Toronto, ON M5V 3L9": ("CA", "sure"),
        "Vancouver, British Columbia": ("CA", "sure"), "Morrison, Colorado": ("US", "sure"),
        "Mexico City": ("MX", "sure"), "Guadalajara, Jalisco": ("MX", "sure"),
        "Austin, TX, USA": ("US", "sure"), "Montréal, Canada": ("CA", "sure"),
        "Toronto, CA": ("CA", "sure"), "Vienna, VA": ("US", "maybe"),
        "Bonn, DE": ("US", "maybe"), "Utrecht, NL": ("CA", "maybe"),
        "Rochester": ("US", "maybe"), "Rochester, UK": (None, "foreign"),
        "Halifax, England": (None, "foreign"), "Laval, France": (None, "foreign"),
        "Bonn, Germany": (None, "foreign"), "Ely, UK": (None, "foreign"),
        "Tbilisi, Georgia": ("US", "maybe"), "Bremen": (None, "unknown"),
        "Padova": (None, "unknown"), "Stuttgart, Baden-Württemberg": (None, "unknown"),
        "Vienna, Vienna": (None, "unknown"), "": (None, "unknown"),
    }
    bad = [(a, want, locate(a)) for a, want in cases.items() if locate(a) != want]
    us = {"is-starts-at-known": True, "starts-at": "2027-06-11T00:00:00Z",
          "starts-at-date-local": "2027-06-10"}
    eu = {"is-starts-at-known": True, "starts-at": "2027-03-06T18:30:00.000Z",
          "starts-at-date-local": "2027-03-06"}
    if clock_verdict(us) != "americas" or clock_verdict(eu) != "other":
        bad.append(("clock heuristic", "ok", "broken"))
    for a, want, got in bad:
        print("FAIL %r: want %r got %r" % (a, want, got))
    print("selftest: %d/%d ok" % (len(cases) + 1 - len(bad), len(cases) + 1))
    return 0 if not bad else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="jethrod", description=__doc__.split("\n")[0],
                                epilog="see man jethrod(1) for signals, files and exit codes")
    p.add_argument("--version", action="version", version="jethrod " + VERSION)
    p.add_argument("-c", "--config", metavar="FILE", help="config file (default: %s)" % CONFIG_FILE)
    p.add_argument("--state-dir", metavar="DIR", help="state directory (default: %s)" % STATE_DIR)
    p.add_argument("--pidfile", metavar="FILE",
                   help="pid/lock file (default: <state-dir>/jethrod.pid)")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")
    r = sub.add_parser("run", help="run the daemon (foreground unless --daemon)")
    r.add_argument("-d", "--daemon", action="store_true",
                   help="detach (double fork) - for sysvinit; systemd/launchd don't need it")
    r.add_argument("-f", "--foreground", action="store_true", help="stay attached (default)")
    r.add_argument("--log", nargs="?", const="", metavar="FILE",
                   help="log to FILE (no FILE: <state-dir>/jethrod.log)")
    r.add_argument("--syslog", action="store_true", help="log to syslog (facility daemon)")
    r.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub.add_parser("stop", help="stop the running daemon (SIGTERM)")
    sub.add_parser("reload", help="reload config and reopen logs (SIGHUP)")
    k = sub.add_parser("kick", help="make the running daemon check now (SIGUSR1)")
    k.add_argument("--site", action="store_true", help="run the site digest now instead (SIGUSR2)")
    sub.add_parser("install-service",
                   help="start now and at login (systemd user unit / Windows Startup)")
    sub.add_parser("uninstall-service", help="undo install-service")
    sub.add_parser("check", help="check the tour feed once; exit 0, or 2 if new NA dates")
    s = sub.add_parser("site", help="run the jethrotull.com change digest once")
    s.add_argument("--recent", action="store_true",
                   help="just list the 10 most recently changed things on the site")
    sub.add_parser("status", help="daemon state, alert history, every booked date")
    sub.add_parser("test-alert", help="fire a test notification on every channel")
    sub.add_parser("selftest", help="verify the geography logic")
    c = sub.add_parser("config", help="show config, or set KEY VALUE")
    c.add_argument("key", nargs="?")
    c.add_argument("value", nargs="?")
    sub.add_parser("forget", help="clear alert history and the site baseline")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # cp1252 consoles vs "Poznań"
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    set_paths(args.config, args.state_dir, args.pidfile)
    cfg = load_config()

    if args.cmd == "run":
        log_file = (args.log or LOG_FILE) if args.log is not None else None
        if args.daemon:
            if WINDOWS:
                p.error("--daemon is POSIX-only; on Windows use install-service")
            if not log_file and not args.syslog:
                log_file = LOG_FILE  # stdout is about to become /dev/null
            daemonize()
        if log_file:
            log_file = os.path.abspath(log_file)
        return Daemon(log_file, args.syslog, args.verbose).run()
    if args.cmd in ("stop", "reload", "kick"):
        if args.cmd == "stop":
            pid = stop_daemon()
        elif args.cmd == "reload" and hasattr(signal, "SIGHUP"):
            pid = signal_daemon(signal.SIGHUP)
        elif args.cmd == "kick" and hasattr(signal, "SIGUSR1"):
            pid = signal_daemon(signal.SIGUSR2 if args.site else signal.SIGUSR1)
        else:
            p.error("%s needs POSIX signals; on Windows: jethrod install-service" % args.cmd)
        if not pid:
            print("jethrod is not running (%s)" % PID_FILE)
            return 3  # LSB: program is not running
        print("%s -> pid %d" % (args.cmd, pid))
        return 0
    if args.cmd == "install-service":
        return install_service()
    if args.cmd == "uninstall-service":
        return uninstall_service()
    if args.cmd == "check":
        fresh, _ = check_once(cfg)
        return 2 if fresh else 0
    if args.cmd == "site":
        if args.recent:
            snap = sorted(site_snapshot().values(), key=lambda i: i["modified"], reverse=True)
            for it in snap[:10]:
                print("%s  %-11s %s\n%s%s" % (it["modified"][:10], it["label"], it["title"],
                                              " " * 25, it["link"]))
        else:
            site_digest(cfg)
        return 0
    if args.cmd == "status":
        pid = running_pid()
        state = load_state()
        print("daemon:  %s" % ("running, pid %d" % pid if pid else "not running"))
        print("config:  %s   ntfy: %s" % (CONFIG_FILE, cfg.get("ntfy_topic") or "off"))
        print("state:   %s   alerted: %d   last check: %s   last site digest: %s" % (
            STATE_DIR, len(state.get("alerted", {})), state.get("last_check") or "never",
            state.get("site_last_run") or "never"))
        if WINDOWS:
            print("startup: %s" % ("yes" if os.path.exists(startup_shortcut()) else "no"))
        evts = events(fetch(cfg["feed"]))
        print("\n%d booked dates (source: %s)" % (len(evts), TOUR_PAGE))
        for e in evts:
            mark = ">>" if (e["region"] or e["clock_hint"]) else "  "
            print(" %s %s" % (mark, describe(e)))
        return 0 if pid else 3
    if args.cmd == "test-alert":
        ch = notify(cfg, "jethrod test", "Bungle in the jungle? No - just a test. "
                    "You'll hear it like this when Tull books North America.")
        print("sent via: %s" % (", ".join(ch) or "nothing"))
        return 0 if ch else 1
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "config":
        if args.key and args.value is not None:
            save_config_value(args.key, args.value)
            print("%s=%s  (apply: jethrod reload)" % (args.key, args.value))
        else:
            for key in DEFAULTS:
                print("%s=%s" % (key, cfg.get(key, "")))
        return 0
    if args.cmd == "forget":
        save_state({"alerted": {}, "last_check": None, "last_total": None})
        print("forgot everything. Thick as a brick.")
        return 0
    print(BANNER)
    p.print_help()
    return 0


def cli():
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    cli()
