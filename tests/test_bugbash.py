"""Regression tests from the 2026-11-13 bug bash. Each was red on 0.2.1."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_jethrod import FIXTURE, SCRIPT, load

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POSIX = os.name == "posix"


def run(*args, env=None, **kw):
    return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True,
                          text=True, timeout=60, env=env, **kw)


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp" if POSIX else None)
        self.env = dict(os.environ, XDG_STATE_HOME=self.tmp.name, XDG_CONFIG_HOME=self.tmp.name,
                        JETHROD_DESKTOP="off", JETHROD_NTFY_TOPIC="", JETHROD_SITE_WATCH="off")
        self.env.pop("JETHROD_DEBUG", None)
        os.environ["XDG_STATE_HOME"] = self.tmp.name
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        self.j = load()

    def tearDown(self):
        self.tmp.cleanup()

    def feed_with(self, address, eid="us-show"):
        with open(FIXTURE, encoding="utf-8") as fh:
            doc = json.load(fh)
        ev = json.loads(json.dumps(doc["included"][0]))
        ev["id"] = eid
        ev["attributes"]["formatted-address"] = address
        doc["included"].append(ev)
        path = os.path.join(self.tmp.name, "feed.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        return "file://" + path


class A_CliErrors(Env):
    def test_unreachable_feed_is_one_line_not_a_traceback(self):
        for cmd in ("check", "status"):
            with self.subTest(cmd=cmd):
                r = run(cmd, env=dict(self.env, JETHROD_FEED="file:///nonexistent/feed.json"))
                self.assertEqual(r.returncode, 1)
                self.assertNotIn("Traceback", r.stderr)
                self.assertRegex(r.stderr.strip(), r"^jethrod: /nonexistent/feed.json: ")

    def test_garbage_json_is_one_line(self):
        bad = os.path.join(self.tmp.name, "bad.json")
        with open(bad, "w") as fh:
            fh.write("<html>not json</html>")
        r = run("check", env=dict(self.env, JETHROD_FEED="file://" + bad))
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)

    def test_debug_env_keeps_the_traceback(self):
        r = run("check", env=dict(self.env, JETHROD_FEED="file:///nonexistent/feed.json",
                                  JETHROD_DEBUG="1"))
        self.assertIn("Traceback", r.stderr)


class B_AppleScript(Env):
    def test_unicode_quotes_backslashes_newlines(self):
        lit = self.j.applescript_string('Poznań "Hall"\\x\nnext')
        self.assertEqual(lit, '"Poznań \\"Hall\\"\\\\x\\nnext"')
        self.assertNotIn("\\u", lit)

    def test_mac_fallback_notification_sends_real_unicode(self):
        seen = []
        with mock.patch.object(self.j.sys, "platform", "darwin"), \
                mock.patch.object(self.j, "WINDOWS", False), \
                mock.patch.object(self.j.shutil, "which", lambda b: None), \
                mock.patch.object(self.j.os.path, "exists", lambda p: False), \
                mock.patch.object(self.j.subprocess, "run", lambda cmd, **k: seen.append(cmd)):
            got = self.j.notify(dict(self.j.DEFAULTS, desktop="on", ntfy_topic=""),
                                "Jethro Tull in North America!", "2027-06-10 Poznań - Hall",
                                log=lambda m: None)
        self.assertEqual(got, ["osascript"])
        script = seen[0][-1]
        self.assertIn("Poznań", script)
        self.assertNotIn("\\u", script)

    @unittest.skipUnless(sys.platform == "darwin", "needs osascript")
    def test_osascript_round_trips_the_literal(self):
        text = 'Poznań "Hall" \\ back\nslash'
        out = subprocess.run(["osascript", "-e", "return " + self.j.applescript_string(text)],
                             capture_output=True, text=True, timeout=30).stdout
        self.assertEqual(out.rstrip("\n").replace("\r", "\n"), text)


class C_ConcurrentChecks(Env):
    def test_daemon_and_cli_check_alert_once(self):
        feed = self.feed_with("Austin, TX")
        sent = os.path.join(self.tmp.name, "sent.log")
        code = (
            "import importlib.machinery as m, importlib.util as u, time\n"
            "l = m.SourceFileLoader('j', %r); s = u.spec_from_loader('j', l)\n"
            "j = u.module_from_spec(s); l.exec_module(j)\n"
            "def slow(cfg, t, b, log=print, **k):\n"
            "    open(%r, 'a').write('sent\\n'); time.sleep(0.8); return ['fake']\n"
            "j.notify = slow\n"
            "j.check_once(j.load_config(), log=lambda m: None)\n") % (SCRIPT, sent)
        env = dict(self.env, JETHROD_FEED=feed)
        procs = [subprocess.Popen([sys.executable, "-c", code], env=env) for _ in range(3)]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        with open(sent) as fh:
            self.assertEqual(fh.read().count("sent"), 1)


class D_ForeignBareCities(Env):
    def test_south_american_evening_shows_are_not_north_america(self):
        for city in ("São Paulo", "Buenos Aires", "Santiago", "Lima", "Bogotá"):
            with self.subTest(city=city):
                e = self.j.events({"included": [{"id": "x", "type": "tour-events", "attributes": {
                    "formatted-address": city, "starts-at": "2027-04-11T00:00:00Z",
                    "starts-at-date-local": "2027-04-10", "is-starts-at-known": True}}]})[0]
                self.assertEqual(self.j.interesting([e]), [])

    def test_a_qualifier_still_wins(self):
        self.assertEqual(self.j.locate("Lima, OH"), ("US", "sure"))
        self.assertEqual(self.j.locate("Santiago, Chile"), (None, "foreign"))


class E_NoStdout(Env):
    def test_pythonw_without_stdout_exits_cleanly(self):
        code = ("import sys; sys.stdout = None; sys.argv = ['jethrod', 'selftest']\n"
                "exec(compile(open(%r, encoding='utf-8').read(), 'jethrod', 'exec'))") % SCRIPT
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=60, env=self.env)
        self.assertNotIn("AttributeError", r.stderr)
        self.assertEqual(r.returncode, 0)


class F_StateShape(Env):
    def test_state_missing_keys_still_alerts(self):
        for content in ('{"site": {}}', '{"alerted": []}', '[]', '"x"'):
            with self.subTest(state=content):
                os.makedirs(os.path.dirname(self.j.STATE_FILE), exist_ok=True)
                with open(self.j.STATE_FILE, "w") as fh:
                    fh.write(content)
                self.j.notify = lambda *a, **k: ["fake"]
                cfg = dict(self.j.DEFAULTS, feed=self.feed_with("Austin, TX"))
                fresh, _ = self.j.check_once(cfg, log=lambda m: None)
                self.assertEqual([e["id"] for e in fresh], ["us-show"])


class G_H_Text(Env):
    def test_help_description_is_a_whole_sentence(self):
        out = run("--help", env=self.env).stdout
        self.assertIn("North America and for changes on jethrotull.com", " ".join(out.split()))

    def test_config_hint_matches_the_platform(self):
        import io
        for windows, want, banned in ((True, "install-service", "jethrod reload"),
                                      (False, "jethrod reload", "install-service")):
            with self.subTest(windows=windows), mock.patch.object(self.j, "WINDOWS", windows), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                self.j.main(["config", "desktop", "on"])
            self.assertIn(want, out.getvalue())
            self.assertNotIn(banned, out.getvalue())


class I_J_Release(unittest.TestCase):
    """Fitness functions for the release surface."""

    def read(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            return fh.read()

    def test_versions_agree(self):
        code = re.search(r'^VERSION = "([^"]+)"', self.read("jethrod.py"), re.M).group(1)
        proj = re.search(r'^version = "([^"]+)"', self.read("pyproject.toml"), re.M).group(1)
        man = re.search(r'^\.TH JETHROD 1 "[^"]*" "jethrod ([^"]+)"', self.read("jethrod.1"),
                        re.M).group(1)
        self.assertEqual({code, proj, man}, {code})

    def test_license_is_an_spdx_string(self):
        py = self.read("pyproject.toml")
        self.assertRegex(py, r'(?m)^license = "MIT"$')
        self.assertRegex(py, r'setuptools>=77')


if __name__ == "__main__":
    unittest.main()
