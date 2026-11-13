"""Pairwise (all-pairs) bug hunts.

Each family lists its factors; `pairwise()` builds the smallest-ish suite in
which every pair of factor values meets at least once (most interaction bugs
need only two things to go wrong together). Every row is checked against an
oracle written from the spec, never by calling the code under test.
"""
import itertools
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from test_jethrod import load


# --------------------------------------------------------------- generator
def covering(factors, strength=2):
    """Greedy t-wise covering array: every combination of `strength` factor values
    appears in at least one row. Deterministic; values need not be hashable."""
    names = list(factors)
    vals = [list(factors[n]) for n in names]
    n = len(names)
    t = min(strength, n)
    todo = {tuple(zip(combo, choice)) for combo in itertools.combinations(range(n), t)
            for choice in itertools.product(*(range(len(vals[i])) for i in combo))}
    rows = []
    while todo:
        row = [None] * n
        for i, a in min(todo):
            row[i] = a
        for k in range(n):
            if row[k] is None:
                def gain(c, k=k):
                    row[k] = c
                    done = [m for m in range(n) if row[m] is not None]
                    g = sum(tuple((m, row[m]) for m in combo) in todo
                            for combo in itertools.combinations(done, t) if k in combo)
                    row[k] = None
                    return g
                row[k] = max(range(len(vals[k])), key=lambda c: (gain(c), -c))
        todo -= {tuple((m, row[m]) for m in combo) for combo in itertools.combinations(range(n), t)}
        rows.append({names[k]: vals[k][row[k]] for k in range(n)})
    return rows


def pairwise(factors):
    return covering(factors, 2)


def covers(factors, rows, strength=2):
    for combo in itertools.combinations(factors, min(strength, len(factors))):
        want = set(itertools.product(*(range(len(factors[x])) for x in combo)))
        got = {tuple(factors[x].index(r[x]) for x in combo) for r in rows}
        if want - got:
            return False
    return True


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = self.tmp.name
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith("JETHROD_"):
                del os.environ[k]
        self.j = load()
        self.j.set_paths(config=os.path.join(self.tmp.name, "user.conf"))
        self.j.SYSTEM_CONFIG = os.path.join(self.tmp.name, "system.conf")

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("JETHROD_"):
                del os.environ[k]
        self.tmp.cleanup()

    def rows(self, factors, strength=2):
        rows = covering(factors, strength)
        self.assertTrue(covers(factors, rows, strength))
        return rows


CLOCKS = {  # (starts-at UTC, local date, starts-at known)
    "americas": ("2027-06-11T00:30:00Z", "2027-06-10", True),     # 8:30pm EDT
    "europe": ("2027-03-06T18:30:00Z", "2027-03-06", True),       # 7:30pm CET
    "unknown": ("2027-03-06T18:30:00Z", "2027-03-06", False),
}


def tour_event(eid, address, clock, sold_out=False):
    ts, local, known = CLOCKS[clock]
    return {"id": eid, "type": "tour-events", "attributes": {
        "formatted-address": address, "venue-name": "Hall " + eid, "is-sold-out": sold_out,
        "starts-at": ts, "starts-at-date-local": local, "is-starts-at-known": known}}


# ======================================================== 0. the generator
class GeneratorTest(unittest.TestCase):
    def test_covers_every_tuple_and_beats_the_full_product(self):
        f = {"a": range(5), "b": range(4), "c": range(4), "d": range(3), "e": range(2)}
        f = {k: list(v) for k, v in f.items()}
        rows = pairwise(f)
        self.assertTrue(covers(f, rows, 2))
        self.assertLessEqual(len(rows), 30)        # full product is 480
        self.assertEqual(rows, pairwise(f))        # deterministic
        rows3 = covering(f, 3)
        self.assertTrue(covers(f, rows3, 3))
        self.assertLess(len(rows3), 480)
        self.assertFalse(covers(f, rows, 3))       # pairs alone miss triples


# ========================================= 1. where is this show, really?
CITIES = {"Chicago": ("US", "unique"), "Toronto": ("CA", "unique"),
          "Rochester": ("US", "dup"), "Halifax": ("CA", "dup"),   # namesakes in the UK
          "Bonn": (None, None), "Lenox": (None, None)}
QUALS = {  # text: (kind, region, collides with an ISO country code)
    "": ("none", None, False), "TX": ("code", "US", False), "tx 78701": ("code", "US", False),
    "DE": ("code", "US", True), "NL": ("code", "CA", True), "ON M5V 3L9": ("code", "CA", False),
    "Texas": ("name", "US", False), "USA": ("country", "US", False),
    "UK": ("foreign", None, False), "Germany": ("foreign", None, False),
    "Baden-Württemberg": ("unknown", None, False), "Mass.": ("unknown", None, False),
}
FORMATS = {"plain": lambda c, q: "%s, %s" % (c, q) if q else c,
           "padded": lambda c, q: "  %s ,   %s  " % (c, q) if q else "  %s  " % c,
           "shouty": lambda c, q: ("%s, %s" % (c, q) if q else c).upper()}


def expect_place(city, qual, clock):
    """-> (region or None, alerts?)"""
    creg, ckind = CITIES[city]
    kind, qreg, collides = QUALS[qual]
    if kind == "foreign":
        return None, False                       # an explicit foreign country wins
    if kind in ("code", "name", "country"):
        reg, maybe = qreg, collides and creg != qreg
    elif creg:
        reg, maybe = creg, ckind == "dup"
    else:
        return None, clock == "americas"         # nothing to go on but the clock
    if maybe and clock == "europe":
        return None, False                       # "Bonn, DE" at 19:30 CET is Germany
    return reg, True


class PlacementTest(Base):
    def test_pairwise_placement(self):
        # 3-wise: "namesake city x no qualifier x European showtime" is a triple
        f = {"city": list(CITIES), "qual": list(QUALS), "clock": list(CLOCKS),
             "fmt": list(FORMATS)}
        for r in self.rows(f, strength=3):
            addr = FORMATS[r["fmt"]](r["city"], r["qual"])
            with self.subTest(addr=addr, clock=r["clock"]):
                e = self.j.events({"included": [tour_event("x", addr, r["clock"])]})[0]
                got = (e["region"], bool(self.j.interesting([e])))
                self.assertEqual(got, expect_place(r["city"], r["qual"], r["clock"]))


# ================================================ 2. alert exactly once
class AlertPipelineTest(Base):
    KINDS = {  # address, clock, should alert
        "na": ("Austin, TX", "europe", True),
        "foreign": ("Bonn, Germany", "americas", False),
        "hint": ("Lenox", "americas", True),
        "maybe_us": ("Vienna, VA", "americas", True),
        "maybe_eu": ("Bonn, DE", "europe", False),
    }

    def test_pairwise_alert_pipeline(self):
        f = {"kind": list(self.KINDS), "prior": ["fresh", "same_id", "other_id"],
             "delivery": ["ok", "fail"], "sold_out": [False, True], "noise": [0, 3]}
        # 3-wise: "alertable x not yet alerted x delivery failed" is a triple
        for n, r in enumerate(self.rows(f, strength=3)):
            with self.subTest(**r):
                addr, clock, alerts = self.KINDS[r["kind"]]
                doc = {"included": [tour_event("show", addr, clock, r["sold_out"])] +
                       [tour_event("eu%d" % i, "Graz%d, Austria" % i, "europe")
                        for i in range(r["noise"])]}
                feed = os.path.join(self.tmp.name, "feed%d.json" % n)
                with open(feed, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh)
                self.j.STATE_FILE = os.path.join(self.tmp.name, "state%d.json" % n)
                prior = {"fresh": {}, "same_id": {"show": "x"}, "other_id": {"old": "x"}}
                self.j.save_state({"alerted": prior[r["prior"]]})
                calls = []

                def fake(cfg, title, body, log=print, ok=True, **kw):
                    calls.append(body)
                    return ["fake"] if ok else []
                cfg = dict(self.j.DEFAULTS, feed="file://" + feed)

                self.j.notify = lambda *a, **k: fake(*a, ok=r["delivery"] == "ok", **k)
                self.j.check_once(cfg, log=lambda m: None)
                first = alerts and r["prior"] != "same_id"
                self.assertEqual(len(calls), int(first))
                self.j.notify = lambda *a, **k: fake(*a, ok=True, **k)
                self.j.check_once(cfg, log=lambda m: None)      # delivery now works
                retry = first and r["delivery"] == "fail"
                self.assertEqual(len(calls), int(first) + int(retry))
                self.j.check_once(cfg, log=lambda m: None)      # and never again
                self.assertEqual(len(calls), int(first) + int(retry))
                for body in calls:
                    self.assertIn(addr.split(",")[0], body)
                    self.assertNotIn("Graz", body)


# ================================================== 3. the site digest
class SiteDigestPairwiseTest(Base):
    TITLES = {"plain": ("Tour news", "Tour news"),
              "entity": ("Ian &#8211; BBC", "Ian – BBC"),
              "tags": ("<em>New</em> album", "New album"),
              "empty": ("", "(untitled)")}

    def site(self, where, title_html, extra=None):
        site = {b: [{"id": 100 + i, "link": "https://jethrotull.com/%s/%d/" % (b, i),
                     "modified_gmt": "2026-01-0%dT10:00:00" % (i + 1),
                     "title": {"rendered": "%s item %d" % (b, i)}} for i in range(3)]
                for b, _ in self.j.SITE_TYPES}
        site[where].insert(0, {"id": 7, "link": "https://jethrotull.com/x/",
                               "modified_gmt": "2026-11-10T09:55:43",
                               "title": {"rendered": title_html}})
        return site

    def test_pairwise_site_digest(self):
        f = {"baseline": ["none", "present"],
             "change": ["none", "updated", "new", "older", "same_time_new_title"],
             "where": ["posts", "pages", "merch"], "delivery": ["ok", "fail"],
             "outage": ["none", "video_down_at_baseline", "video_down_now"],
             "title": list(self.TITLES)}
        for n, r in enumerate(self.rows(f)):
            with self.subTest(**r):
                self.j.STATE_FILE = os.path.join(self.tmp.name, "site%d.json" % n)
                html_title, plain_title = self.TITLES[r["title"]]
                site = self.site(r["where"], html_title)
                down = set()
                sent = []

                def get(url):
                    base = url.split("/wp/v2/")[1].split("?")[0]
                    if base in down:
                        raise OSError("503")
                    return json.loads(json.dumps(site[base]))

                def notifier(ok):
                    def fake(cfg, title, body, log=print, **kw):
                        sent.append((title, body, kw))
                        return ["fake"] if ok else []
                    return fake

                def digest(ok=True):
                    return self.j.site_digest(self.j.DEFAULTS, log=lambda m: None, get=get,
                                              notify_fn=notifier(ok))

                if r["outage"] == "video_down_at_baseline":
                    down = {"video-content"}
                if r["baseline"] == "present":
                    digest()
                    self.assertEqual(sent, [], "baseline must be silent")
                down = {"video-content"} if r["outage"] == "video_down_now" else set()

                item = site[r["where"]][0]
                if r["change"] == "updated":
                    item["modified_gmt"] = "2026-11-14T08:00:00"
                elif r["change"] == "new":
                    site[r["where"]].insert(0, dict(item, id=8, modified_gmt="2026-11-14T09:00:00"))
                elif r["change"] == "older":
                    item["modified_gmt"] = "2025-01-01T00:00:00"
                elif r["change"] == "same_time_new_title":
                    item["title"] = {"rendered": "retitled"}

                digest(ok=r["delivery"] == "ok")
                alerts = r["baseline"] == "present" and r["change"] in ("updated", "new")
                self.assertEqual(len(sent), int(alerts))
                if alerts:
                    title, body, kw = sent[0]
                    self.assertEqual(title, "jethrotull.com: 1 update")
                    self.assertEqual(body.startswith("New "), r["change"] == "new")
                    self.assertIn(plain_title, body)
                    self.assertNotIn("video-content", body)
                    self.assertEqual(kw["priority"], "default")
                digest()                               # a failed delivery retries once
                retry = alerts and r["delivery"] == "fail"
                self.assertEqual(len(sent), int(alerts) + int(retry))
                down = set()
                digest()                               # video back: no storm, no repeats
                self.assertEqual(len(sent), int(alerts) + int(retry))


# ============================================ 4. which setting wins
class ConfigPrecedenceTest(Base):
    def write(self, path, key, value, style):
        line = {"plain": "%s=%s", "comment": "%s=%s   # my phone",
                "spaced": "   %s   =   %s   ", "crlf": "%s=%s", "bom": "%s=%s"}[style] % (key, value)
        text = line + "\n# trailing comment\ninterval_minutes=30\n"
        if style == "crlf":
            text = text.replace("\n", "\r\n")
        data = text.encode("utf-8")
        if style == "bom":
            data = b"\xef\xbb\xbf" + data             # Windows Notepad
        with open(path, "wb") as fh:
            fh.write(data)

    def test_pairwise_precedence(self):
        values = {"ntfy_topic": ("sys-topic", "user-topic", "env-topic"),
                  "digest_time": ("06:00", "07:00", "09:00")}
        f = {"key": list(values), "system": ["absent", "plain", "comment"],
             "user": ["absent", "plain", "comment", "spaced", "crlf", "bom"],
             "env": ["unset", "set"]}
        for r in self.rows(f):
            with self.subTest(**r):
                key = r["key"]
                sys_v, user_v, env_v = values[key]
                for p in (self.j.SYSTEM_CONFIG, self.j.CONFIG_FILE):
                    if os.path.exists(p):
                        os.remove(p)
                os.environ.pop("JETHROD_" + key.upper(), None)
                if r["system"] != "absent":
                    self.write(self.j.SYSTEM_CONFIG, key, sys_v, r["system"])
                if r["user"] != "absent":
                    self.write(self.j.CONFIG_FILE, key, user_v, r["user"])
                if r["env"] == "set":
                    os.environ["JETHROD_" + key.upper()] = env_v
                want = (env_v if r["env"] == "set" else user_v if r["user"] != "absent"
                        else sys_v if r["system"] != "absent" else self.j.DEFAULTS[key])
                self.assertEqual(self.j.load_config()[key], want)


# ============================================== 5. the morning schedule
class ScheduleTest(Base):
    def test_pairwise_site_due(self):
        times = {"08:00": (8, 0), "8:05": (8, 5), "23:59": (23, 59), " 07:30 ": (7, 30),
                 "24:00": (8, 0), "7:60": (8, 0), "garbage": (8, 0), "": (8, 0)}
        watch = {"on": True, "yes": True, "off": False, "OFF": False, " off ": False, "0": False}
        f = {"digest_time": list(times), "offset_min": [-1, 0, 1, 600],
             "last_run": ["never", "today", "yesterday"], "watch": list(watch)}
        for r in self.rows(f):
            with self.subTest(**r):
                hh, mm = times[r["digest_time"]]
                now = datetime(2026, 11, 14, hh, mm) + timedelta(minutes=r["offset_min"])
                last = {"never": None, "today": now.date().isoformat(),
                        "yesterday": (now.date() - timedelta(days=1)).isoformat()}[r["last_run"]]
                cfg = dict(self.j.DEFAULTS, digest_time=r["digest_time"], site_watch=r["watch"])
                want = (watch[r["watch"]] and last != now.date().isoformat()
                        and (now.hour, now.minute) >= (hh, mm))
                self.assertEqual(self.j.site_due(cfg, {"site_last_run": last}, now), want)

    def test_interval_is_clamped(self):
        cases = {"30": 30, "0": 5, "-5": 5, "abc": 30, "inf": 30, "nan": 30,
                 "1e9": 1440, "4.5": 5, " 60 ": 60}
        for raw, minutes in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.j.Daemon.interval({"interval_minutes": raw}), minutes * 60)


# ============================================ 6. which channel fires
class NotifyRoutingTest(Base):
    PLATFORMS = {  # sys.platform, WINDOWS, binaries on PATH, expected desktop channel
        "mac_tn": ("darwin", False, {"terminal-notifier"}, "terminal-notifier"),
        "mac_plain": ("darwin", False, set(), "osascript"),
        "windows": ("win32", True, set(), "windows-toast"),
        "linux_ns": ("linux", False, {"notify-send"}, "notify-send"),
        "linux_bare": ("linux", False, set(), None),
    }

    def test_pairwise_routing(self):
        f = {"platform": list(self.PLATFORMS), "desktop": ["on", "off", " OFF ", "false", ""],
             "topic": ["jethrod-x", "", "   "], "failure": ["none", "desktop", "ntfy"],
             "priority": ["high", "default"], "title": ["ascii", "Poznań"]}
        for r in self.rows(f):
            with self.subTest(**r):
                plat, win, bins, chan = self.PLATFORMS[r["platform"]]
                posted = []

                def run(cmd, **kw):
                    if r["failure"] == "desktop":
                        raise self.j.subprocess.CalledProcessError(1, cmd)

                def ps(script, timeout=60):
                    run(["powershell.exe"])

                def urlopen(req, timeout=None):
                    if r["failure"] == "ntfy":
                        raise OSError("ntfy down")
                    posted.append(json.loads(req.data.decode("utf-8")))
                    return mock.Mock(read=lambda: b"{}")

                title = "jethrod " + r["title"]
                with mock.patch.object(self.j.sys, "platform", plat), \
                        mock.patch.object(self.j, "WINDOWS", win), \
                        mock.patch.object(self.j.shutil, "which",
                                          lambda b: "/usr/bin/" + b if b in bins else None), \
                        mock.patch.object(self.j.os.path, "exists", lambda p: False), \
                        mock.patch.object(self.j.subprocess, "run", run), \
                        mock.patch.object(self.j, "powershell", ps), \
                        mock.patch.object(self.j.urllib.request, "urlopen", urlopen):
                    got = self.j.notify(dict(self.j.DEFAULTS, desktop=r["desktop"],
                                             ntfy_topic=r["topic"]),
                                        title, "Toronto, ON - Massey Hall", log=lambda m: None,
                                        click="https://jethrotull.com/x/", priority=r["priority"])
                want = []
                if r["desktop"].strip().lower() not in ("off", "false") and chan \
                        and r["failure"] != "desktop":
                    want.append(chan)
                if r["topic"].strip() and r["failure"] != "ntfy":
                    want.append("ntfy")
                self.assertEqual(got, want)
                if "ntfy" in want:
                    msg = posted[0]
                    self.assertEqual((msg["topic"], msg["title"], msg["click"]),
                                     (r["topic"].strip(), title, "https://jethrotull.com/x/"))
                    self.assertEqual(msg["priority"], 4 if r["priority"] == "high" else 3)


if __name__ == "__main__":
    unittest.main()
