import copy
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "jethrod.py")
FIXTURE = os.path.join(HERE, "feed_2026-11-13.json")


def load():
    loader = importlib.machinery.SourceFileLoader("jethrod", SCRIPT)
    spec = importlib.util.spec_from_loader("jethrod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class JethrodTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = self.tmp.name
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        self.j = load()
        self.sent = []
        self.j.notify = lambda cfg, t, b, log=print, **kw: self.sent.append((t, b, kw)) or ["fake"]
        self.cfg = dict(self.j.DEFAULTS, feed="file://" + FIXTURE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_feed_has_no_north_america(self):
        evts = self.j.events(self.j.fetch(self.cfg["feed"]))
        self.assertEqual(len(evts), 26)
        self.assertEqual(self.j.interesting(evts), [])

    def test_selftest_passes(self):
        self.assertEqual(self.j.selftest(), 0)

    def _with_us_show(self):
        with open(FIXTURE) as fh:
            doc = json.load(fh)
        show = copy.deepcopy(doc["included"][0])
        show["id"] = "fake-us-show"
        show["attributes"].update({
            "formatted-address": "Saratoga Springs, NY",
            "venue-name": "Saratoga Performing Arts Center",
            "starts-at": "2027-08-01T00:00:00Z",
            "starts-at-date-local": "2027-07-31"})
        doc["included"].append(show)
        path = os.path.join(self.tmp.name, "feed.json")
        with open(path, "w") as fh:
            json.dump(doc, fh)
        return dict(self.cfg, feed="file://" + path)

    def test_alerts_once_then_stays_quiet(self):
        cfg = self._with_us_show()
        fresh, _ = self.j.check_once(cfg, log=lambda m: None)
        self.assertEqual([e["id"] for e in fresh], ["fake-us-show"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Saratoga", self.sent[0][1])
        fresh, _ = self.j.check_once(cfg, log=lambda m: None)
        self.assertEqual(fresh, [])
        self.assertEqual(len(self.sent), 1)

    def test_city_only_address_caught_by_clock(self):
        cfg = self._with_us_show()
        with open(cfg["feed"][7:]) as fh:
            doc = json.load(fh)
        doc["included"][-1]["attributes"]["formatted-address"] = "Lenox"
        with open(cfg["feed"][7:], "w") as fh:
            json.dump(doc, fh)
        fresh, _ = self.j.check_once(cfg, log=lambda m: None)
        self.assertEqual(len(fresh), 1)
        self.assertTrue(fresh[0]["clock_hint"])

    def test_failed_delivery_retries_next_time(self):
        cfg = self._with_us_show()
        self.j.notify = lambda cfg, t, b, log=print, **kw: []
        self.j.check_once(cfg, log=lambda m: None)
        self.j.notify = lambda cfg, t, b, log=print, **kw: ["fake"]
        fresh, _ = self.j.check_once(cfg, log=lambda m: None)
        self.assertEqual(len(fresh), 1)


class SiteDigestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = self.tmp.name
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        self.j = load()
        self.site = {
            "posts": [{"id": 1, "link": "https://jethrotull.com/news-1/",
                       "modified_gmt": "2026-08-03T07:01:47",
                       "title": {"rendered": "Ian on the radio &#8211; BBC"}}],
            "pages": [{"id": 2, "link": "https://jethrotull.com/management/",
                       "modified_gmt": "2026-11-10T09:55:43",
                       "title": {"rendered": "Management"}}],
        }
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, url):
        base = url.split("/wp/v2/")[1].split("?")[0]
        if base not in self.site:
            raise OSError("404")
        return self.site[base]

    def notify(self, cfg, title, body, log=print, **kw):
        self.sent.append((title, body, kw))
        return ["fake"]

    def digest(self):
        return self.j.site_digest(self.j.DEFAULTS, log=lambda m: None,
                                  get=self.get, notify_fn=self.notify)

    def test_first_run_is_a_silent_baseline(self):
        self.assertEqual(self.digest(), [])
        self.assertEqual(self.sent, [])

    def test_new_and_updated_items_make_one_low_priority_digest(self):
        self.digest()
        self.site["pages"][0]["modified_gmt"] = "2026-11-14T08:00:00"
        self.site["posts"].insert(0, {"id": 3, "link": "https://jethrotull.com/new-album/",
                                      "modified_gmt": "2026-11-14T09:00:00",
                                      "title": {"rendered": "New album <em>announced</em>"}})
        changes = self.digest()
        self.assertEqual(sorted(k for k, _ in changes), ["new", "updated"])
        self.assertEqual(len(self.sent), 1)
        title, body, kw = self.sent[0]
        self.assertEqual(title, "jethrotull.com: 2 updates")
        self.assertIn("New News: New album announced", body)
        self.assertIn("Page: Management", body)
        self.assertEqual(kw["priority"], "default")
        self.assertEqual(self.digest(), [])  # nothing twice

    def test_unchanged_site_says_nothing(self):
        self.digest()
        self.digest()
        self.assertEqual(self.sent, [])

    def test_html_entities_in_titles(self):
        self.assertEqual(self.j._plain("Ian on the radio &#8211; BBC"), "Ian on the radio \u2013 BBC")

    def test_due_once_per_day_after_digest_time(self):
        import datetime as dt
        cfg = dict(self.j.DEFAULTS, digest_time="08:00")
        early = dt.datetime(2026, 11, 14, 7, 59)
        late = dt.datetime(2026, 11, 14, 8, 0)
        self.assertFalse(self.j.site_due(cfg, {}, early))
        self.assertTrue(self.j.site_due(cfg, {}, late))
        self.assertFalse(self.j.site_due(cfg, {"site_last_run": "2026-11-14"}, late))
        self.assertFalse(self.j.site_due(dict(cfg, site_watch="off"), {}, late))


class WindowsBitsTest(unittest.TestCase):
    def setUp(self):
        self.j = load()

    def test_windows_dirs(self):
        cfg, st = self.j.app_dirs("nt", {"APPDATA": "C:\\R", "LOCALAPPDATA": "C:\\L"})
        self.assertTrue(cfg.startswith("C:\\R") and cfg.endswith("jethrod"))
        self.assertTrue(st.startswith("C:\\L") and st.endswith("jethrod"))

    def test_toast_escapes_xml_and_powershell_quotes(self):
        s = self.j.windows_toast_script("Tull's <big> news", "a & b\nc", click="https://x/?a=1&b=2")
        self.assertIn("Tull&#x27;s &lt;big&gt; news", s)
        self.assertIn("a &amp; b | c", s)
        self.assertIn("https://x/?a=1&amp;b=2", s)

    def test_ps_quote(self):
        self.assertEqual(self.j.ps_quote("it's"), "'it''s'")


POSIX = os.name == "posix"


class DaemonPlumbingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp" if POSIX else None)
        os.environ["XDG_STATE_HOME"] = self.tmp.name
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        self.j = load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_paths_follow_systemd_then_root_fhs(self):
        cfg, st = self.j.app_dirs("posix", {"STATE_DIRECTORY": "/var/lib/jethrod",
                                            "CONFIGURATION_DIRECTORY": "/etc/jethrod"}, euid=1000)
        self.assertEqual((cfg, st), ("/etc/jethrod", "/var/lib/jethrod"))
        self.assertEqual(self.j.app_dirs("posix", {}, euid=0), ("/etc/jethrod", "/var/lib/jethrod"))
        cfg, st = self.j.app_dirs("posix", {"XDG_CONFIG_HOME": "/x", "XDG_STATE_HOME": "/y"}, euid=0)
        self.assertEqual((cfg, st), ("/x/jethrod", "/y/jethrod"))

    def test_runtime_directory_gets_the_pidfile(self):
        self.j.set_paths(env={"RUNTIME_DIRECTORY": "/run/jethrod", "XDG_STATE_HOME": "/s"})
        self.assertEqual(self.j.PID_FILE, os.path.join("/run/jethrod", "jethrod.pid"))
        self.j.set_paths(pidfile="/tmp/p.pid", env={})
        self.assertEqual(self.j.PID_FILE, "/tmp/p.pid")

    def test_pidfile_is_an_exclusive_lock(self):
        path = os.path.join(self.tmp.name, "j.pid")
        self.j.set_paths(pidfile=path)
        first = self.j.PidFile(path).acquire()
        self.assertEqual(self.j.running_pid(), os.getpid())
        with self.assertRaises(self.j.AlreadyRunning):
            self.j.PidFile(path).acquire()
        first.release()
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(self.j.running_pid())

    def test_stale_pidfile_does_not_block(self):
        path = os.path.join(self.tmp.name, "j.pid")
        with open(path, "w") as fh:
            fh.write("999999\n")
        self.j.set_paths(pidfile=path)
        self.assertIsNone(self.j.running_pid())
        self.j.PidFile(path).acquire().release()

    def test_watchdog_interval(self):
        self.assertIsNone(self.j.watchdog_interval({}))
        self.assertEqual(self.j.watchdog_interval({"WATCHDOG_USEC": "600000000"}), 300.0)
        self.assertIsNone(self.j.watchdog_interval({"WATCHDOG_USEC": "1", "WATCHDOG_PID": "1"}))

    @unittest.skipUnless(POSIX, "AF_UNIX datagrams")
    def test_sd_notify_speaks_the_protocol(self):
        import socket
        addr = os.path.join(self.tmp.name, "notify")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        srv.bind(addr)
        try:
            self.assertTrue(self.j.sd_notify("READY=1\nSTATUS=hi", {"NOTIFY_SOCKET": addr}))
            self.assertEqual(srv.recv(64), b"READY=1\nSTATUS=hi")
        finally:
            srv.close()
        self.assertFalse(self.j.sd_notify("READY=1", {}))

    def test_journal_formatter_prefixes_priority(self):
        import logging
        rec = logging.LogRecord("jethrod", logging.WARNING, __file__, 1, "uh oh", None, None)
        self.assertEqual(self.j._JournalFormatter().format(rec), "<4>uh oh")


@unittest.skipUnless(POSIX, "signals + fork")
class DaemonLifecycleTest(unittest.TestCase):
    """Real process: --daemon, pidfile, USR1/USR2/HUP, refusal of a 2nd copy, TERM."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.env = dict(os.environ, XDG_STATE_HOME=self.tmp.name, XDG_CONFIG_HOME=self.tmp.name,
                        JETHROD_FEED="file://" + FIXTURE, JETHROD_DESKTOP="off",
                        JETHROD_SITE_WATCH="off", JETHROD_NTFY_TOPIC="")
        self.env.pop("NOTIFY_SOCKET", None)
        self.pid = os.path.join(self.tmp.name, "j.pid")
        self.log = os.path.join(self.tmp.name, "j.log")

    def tearDown(self):
        self.jet("stop")
        self.tmp.cleanup()

    def jet(self, *args):
        import subprocess
        import sys
        return subprocess.run([sys.executable, SCRIPT, "--pidfile", self.pid] + list(args),
                              env=self.env, capture_output=True, text=True, timeout=60)

    def wait_log(self, needle, count=1, timeout=15):
        import time
        end = time.time() + timeout
        while time.time() < end:
            if os.path.exists(self.log):
                with open(self.log, encoding="utf-8") as fh:
                    if fh.read().count(needle) >= count:
                        return True
            time.sleep(0.2)
        with open(self.log, encoding="utf-8") as fh:
            self.fail("never saw %r x%d in log:\n%s" % (needle, count, fh.read()))

    def test_full_lifecycle(self):
        r = self.jet("run", "--daemon", "--log", self.log)
        self.assertEqual(r.returncode, 0, r.stderr)  # parent returns at once
        self.wait_log("on the books")
        with open(self.pid) as fh:
            pid = int(fh.read())
        self.assertIn("running, pid %d" % pid, self.jet("status").stdout)

        second = self.jet("run", "--log", self.log)
        self.assertEqual(second.returncode, 1)
        self.wait_log("already running")

        self.assertEqual(self.jet("kick").returncode, 0)          # SIGUSR1
        self.wait_log("on the books", count=2)
        self.assertEqual(self.jet("reload").returncode, 0)        # SIGHUP
        self.wait_log("reloaded")

        self.assertEqual(self.jet("stop").returncode, 0)          # SIGTERM
        self.wait_log("stopping")
        self.assertFalse(os.path.exists(self.pid))
        self.assertEqual(self.jet("stop").returncode, 3)          # LSB "not running"


if __name__ == "__main__":
    unittest.main()
