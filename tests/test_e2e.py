"""End-to-end: the real jethrod process against a fake internet.

A local HTTP server plays the three outside services jethrod talks to: the
Seated tour feed, jethrotull.com's WordPress API and an ntfy server. A
stand-in terminal-notifier (macOS) / notify-send (Linux) on PATH records
desktop alerts. Nothing inside jethrod is mocked: every test drives the CLI
or a running daemon and checks only what a user can observe - phone pushes,
desktop notifications, exit codes, stdout/stderr, the log and the pidfile.

    python -m unittest tests.test_e2e                         # this checkout
    JETHROD_E2E_CMD=/opt/homebrew/bin/jethrod python -m ...   # an installed copy
"""
import copy
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_jethrod import SCRIPT

POSIX = os.name == "posix"
CMD = shlex.split(os.environ["JETHROD_E2E_CMD"]) if os.environ.get("JETHROD_E2E_CMD") \
    else [sys.executable, SCRIPT]
TOUR_PAGE = "https://jethrotull.com/tour-dates/"
SITE_BASES = ["posts", "pages", "discography-item", "the-attic", "video-content",
              "gallery", "merch", "band-member"]
CLOCKS = {"europe": ("2027-03-06T18:30:00Z", "2027-03-06"),     # 19:30 CET
          "americas": ("2027-06-11T00:30:00Z", "2027-06-10")}   # 20:30 EDT


def tour_event(eid, address, clock="europe"):
    ts, local = CLOCKS[clock]
    return {"id": eid, "type": "tour-events", "attributes": {
        "formatted-address": address, "venue-name": "Hall " + eid, "is-sold-out": False,
        "starts-at": ts, "starts-at-date-local": local, "is-starts-at-known": True}}


class FakeInternet:
    """Seated + WordPress + ntfy on 127.0.0.1, with switchable outages."""

    def __init__(self):
        self.events = [tour_event("eu1", "Bonn, Germany"), tour_event("eu2", "Ely, UK"),
                       tour_event("eu3", "Padova")]
        self.site = {b: [{"id": i, "link": "https://jethrotull.com/%s/%d/" % (b, i),
                          "modified_gmt": "2026-01-0%dT10:00:00" % i,
                          "title": {"rendered": "%s item %d" % (b, i)}} for i in (1, 2)]
                     for b in SITE_BASES}
        self.down = set()   # any of "feed", "site", "ntfy"
        self.pushes = []
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = self.path.split("?")[0]
                with fake.lock:
                    if path.startswith("/api/tour/"):
                        if "feed" in fake.down:
                            return self.reply(503, {"error": "down"})
                        return self.reply(200, {"data": {"type": "tours", "id": "x"},
                                                "included": copy.deepcopy(fake.events)})
                    if path.startswith("/wp-json/wp/v2/"):
                        base = path.rsplit("/", 1)[1]
                        if "site" in fake.down or base not in fake.site:
                            return self.reply(503, {"error": "down"})
                        items = sorted(fake.site[base], key=lambda i: i["modified_gmt"],
                                       reverse=True)
                        return self.reply(200, copy.deepcopy(items))
                self.reply(404, {})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                with fake.lock:
                    if "ntfy" in fake.down:
                        return self.reply(500, {"error": "down"})
                    fake.pushes.append(json.loads(body))
                self.reply(200, {"id": "ok"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def add_event(self, eid, address, clock="europe"):
        with self.lock:
            self.events.append(tour_event(eid, address, clock))

    def touch(self, base, item_id, modified, title=None):
        with self.lock:
            for it in self.site[base]:
                if it["id"] == item_id:
                    it["modified_gmt"] = modified
                    break
            else:
                self.site[base].append({"id": item_id, "modified_gmt": modified,
                                        "link": "https://jethrotull.com/%s/%d/" % (base, item_id),
                                        "title": {"rendered": title or "new"}})

    def pushes_copy(self):
        with self.lock:
            return list(self.pushes)


class E2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jethrod-e2e-", dir="/tmp" if POSIX else None)
        self.net = FakeInternet()
        self.pid = os.path.join(self.tmp, "jethrod.pid")
        self.log = os.path.join(self.tmp, "jethrod.log")
        self.desktop_log = os.path.join(self.tmp, "desktop.log")
        self.conf = os.path.join(self.tmp, "jethrod.conf")
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        if POSIX:  # stand-in desktop notifier that records what it was asked to show
            name = "terminal-notifier" if sys.platform == "darwin" else "notify-send"
            shim = os.path.join(bindir, name)
            with open(shim, "w") as fh:
                fh.write('#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\n' % self.desktop_log)
            os.chmod(shim, 0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("JETHROD_", "NOTIFY_SOCKET", "JOURNAL_STREAM",
                                         "WATCHDOG_"))}
        self.env.update(
            XDG_STATE_HOME=self.tmp, XDG_CONFIG_HOME=self.tmp,
            PATH=bindir + os.pathsep + os.environ.get("PATH", ""),
            NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        self.write_config(ntfy_topic="e2e-topic")
        self.daemon = None

    def tearDown(self):
        if self.daemon and self.daemon.poll() is None:
            self.daemon.terminate()
            self.daemon.wait(timeout=20)
        self.net.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- helpers
    def write_config(self, **over):
        cfg = {"feed": self.net.url + "/api/tour/x?include=tour-events",
               "site": self.net.url, "ntfy_server": self.net.url + "/ntfy",
               "desktop": "on" if POSIX else "off", "site_watch": "on",
               "digest_time": "00:00", "interval_minutes": "5"}
        cfg.update(over)
        with open(self.conf, "w", encoding="utf-8") as fh:
            fh.write("# written by test_e2e\n")
            fh.writelines("%s=%s\n" % kv for kv in cfg.items())

    def jet(self, *args, env=None):
        return subprocess.run(CMD + ["--config", self.conf, "--pidfile", self.pid] + list(args),
                              env=env or self.env, capture_output=True, text=True, timeout=90)

    def wait_for(self, what, predicate, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return
            time.sleep(0.1)
        self.fail("timed out waiting for %s\n--- log ---\n%s" % (what, self.read(self.log)))

    @staticmethod
    def read(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def log_count(self, needle):
        return self.read(self.log).count(needle)

    def start_daemon(self, expect_site=True):
        rounds = self.log_count("dates on the books") + self.log_count("NEW ")
        self.daemon = subprocess.Popen(
            CMD + ["--config", self.conf, "--pidfile", self.pid, "run", "--log", self.log],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.wait_for("daemon up", lambda: self.log_count("up (pid %d)" % self.daemon.pid))
        self.wait_for("first round", lambda: self.log_count("dates on the books")
                      + self.log_count("NEW ") > rounds)
        if expect_site:  # the daily digest runs once per day, so not after a restart
            self.wait_for("site digest ran", lambda: "site: " in self.read(self.log).split(
                "up (pid %d)" % self.daemon.pid)[-1])

    def round_done(self, before):
        return lambda: self.log_count("dates on the books") + self.log_count("NEW ") > before

    def rounds(self):
        return self.log_count("dates on the books") + self.log_count("NEW ")


# ======================================================== CLI, every OS
class CliEndToEnd(E2E):
    def test_tour_alerts_once_retries_and_reports_outages(self):
        r = self.jet("check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.net.pushes_copy(), [])

        # one real North American date among traps that must stay quiet
        self.net.add_event("us1", "Austin, TX", "americas")
        self.net.add_event("trap-de", "Bonn, DE", "europe")         # Germany, not Delaware
        self.net.add_event("trap-sa", "São Paulo", "americas")      # late UTC, not NA
        self.net.add_event("trap-uk", "Rochester, UK", "europe")    # namesake city
        r = self.jet("check")
        self.assertEqual(r.returncode, 2, r.stderr)
        [push] = self.net.pushes_copy()
        self.assertEqual((push["topic"], push["priority"], push["click"]), ("e2e-topic", 4, TOUR_PAGE))
        self.assertEqual(push["title"], "Jethro Tull in North America!")
        self.assertIn("Austin, TX", push["message"])
        for trap in ("Bonn", "São Paulo", "Rochester"):
            self.assertNotIn(trap, push["message"])
        if POSIX:
            self.assertIn("Austin, TX", self.read(self.desktop_log), r.stdout + r.stderr)

        r = self.jet("check")
        self.assertEqual((r.returncode, len(self.net.pushes_copy())), (0, 1), "alerted twice")

        # phone channel down and desktop off: nothing delivered, so nothing is recorded...
        self.write_config(ntfy_topic="e2e-topic", desktop="off")
        self.net.down.add("ntfy")
        self.net.add_event("ca1", "Toronto, ON", "americas")
        self.assertEqual(self.jet("check").returncode, 2)
        self.assertEqual(len(self.net.pushes_copy()), 1)
        # ...and it is delivered exactly once when the channel comes back
        self.net.down.discard("ntfy")
        self.assertEqual(self.jet("check").returncode, 2)
        pushes = self.net.pushes_copy()
        self.assertEqual(len(pushes), 2)
        self.assertIn("Toronto, ON", pushes[1]["message"])
        self.assertEqual(self.jet("check").returncode, 0)

        # the feed is down: one line on stderr, exit 1, no traceback
        self.net.down.add("feed")
        r = self.jet("check")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("jethrod: "), r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.net.down.discard("feed")

        r = self.jet("status")
        self.assertEqual(r.returncode, 3, "status must say the daemon is not running")
        marked = [l for l in r.stdout.splitlines() if l.lstrip().startswith(">>")]
        self.assertEqual(sorted(l.split("  ")[1].split(" - ")[0].strip() for l in marked),
                         ["Austin, TX", "Toronto, ON"])
        self.assertIn("alerted: 2", r.stdout)

    def test_site_digest_baseline_changes_and_outage(self):
        r = self.jet("site")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.net.pushes_copy(), [], "the first look is a silent baseline")

        self.net.touch("pages", 1, "2026-11-17T08:00:00")                     # edited
        self.net.touch("posts", 9, "2026-11-17T09:00:00", "Tull &#8211; new album")  # new
        self.assertEqual(self.jet("site").returncode, 0)
        [push] = self.net.pushes_copy()
        self.assertEqual((push["title"], push["priority"], push["click"]),
                         ("jethrotull.com: 2 updates", 3, self.net.url))
        self.assertIn("New News: Tull – new album", push["message"])
        self.assertIn("Page: pages item 1", push["message"])

        self.assertEqual(self.jet("site").returncode, 0)
        self.assertEqual(len(self.net.pushes_copy()), 1, "the same change was reported twice")

        r = self.jet("site", "--recent")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Tull – new album", r.stdout.splitlines()[0])

        self.net.down.add("site")
        r = self.jet("site")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("jethrod: "), r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_test_alert_reaches_every_channel(self):
        r = self.jet("test-alert")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ntfy", r.stdout)
        self.assertEqual(self.net.pushes_copy()[0]["title"], "jethrod test")
        if POSIX:
            self.assertIn("jethrod test", self.read(self.desktop_log), r.stdout + r.stderr)


# ===================================================== daemon, POSIX
@unittest.skipUnless(POSIX, "signals")
class DaemonEndToEnd(E2E):
    def test_daemon_story(self):
        self.start_daemon()
        self.assertEqual(self.net.pushes_copy(), [], "startup must be silent")
        r = self.jet("status")
        self.assertEqual(r.returncode, 0)
        self.assertIn("running, pid %d" % self.daemon.pid, r.stdout)

        second = self.jet("run", "--log", self.log)
        self.assertEqual(second.returncode, 1)
        self.wait_for("refusal logged", lambda: self.log_count("already running"))

        # a new North American date: USR1 makes the daemon look now
        self.net.add_event("us1", "Saratoga Springs, NY", "americas")
        self.assertEqual(self.jet("kick").returncode, 0)
        self.wait_for("tour push", lambda: len(self.net.pushes_copy()) == 1)
        self.assertIn("Saratoga Springs, NY", self.net.pushes_copy()[0]["message"])
        self.assertIn("Saratoga Springs, NY", self.read(self.desktop_log), self.read(self.log))

        before = self.rounds()
        self.jet("kick")
        self.wait_for("next round", self.round_done(before))
        self.assertEqual(len(self.net.pushes_copy()), 1, "alerted twice")
        self.assertIn("1 in North America, already reported", self.read(self.log),
                      "the log must not claim there are no North American dates")

        # a site change: USR2 runs the digest now
        self.net.touch("merch", 2, "2026-11-17T12:00:00")
        self.assertEqual(self.jet("kick", "--site").returncode, 0)
        self.wait_for("digest push", lambda: len(self.net.pushes_copy()) == 2)
        self.assertEqual(self.net.pushes_copy()[1]["priority"], 3)

        # feed outage: the daemon logs it and keeps running
        self.net.down.add("feed")
        self.jet("kick")
        self.wait_for("outage logged", lambda: self.log_count("tour check failed"))
        self.assertIsNone(self.daemon.poll())
        self.net.down.discard("feed")

        # HUP: a new config takes effect without a restart
        self.write_config(ntfy_topic="e2e-topic-2")
        self.assertEqual(self.jet("reload").returncode, 0)
        self.wait_for("reload", lambda: self.log_count("reloaded"))
        self.net.add_event("mx1", "Mexico City", "americas")
        self.jet("kick")
        self.wait_for("push to new topic", lambda: len(self.net.pushes_copy()) == 3)
        self.assertEqual(self.net.pushes_copy()[2]["topic"], "e2e-topic-2")

        # TERM: clean exit, pidfile gone
        self.assertEqual(self.jet("stop").returncode, 0)
        self.assertEqual(self.daemon.wait(timeout=20), 0)
        self.assertFalse(os.path.exists(self.pid))
        self.assertIn("stopping", self.read(self.log))
        self.assertEqual(self.jet("status").returncode, 3)

        # restart: nothing already reported is reported again
        self.start_daemon(expect_site=False)
        before = self.rounds()
        self.jet("kick", "--site")
        self.wait_for("round after restart", self.round_done(before))
        time.sleep(1)
        self.assertEqual(len(self.net.pushes_copy()), 3, "restart re-alerted old news")
        self.jet("stop")
        self.assertEqual(self.daemon.wait(timeout=20), 0)

    def test_detached_daemon(self):
        r = self.jet("run", "--daemon", "--log", self.log)
        self.assertEqual(r.returncode, 0, r.stderr)   # the parent returns at once
        self.wait_for("detached daemon up", lambda: self.log_count("dates on the books"))
        with open(self.pid) as fh:
            pid = int(fh.read())
        self.assertIn("running, pid %d" % pid, self.jet("status").stdout)
        self.assertEqual(self.jet("stop").returncode, 0)
        self.wait_for("pidfile removed", lambda: not os.path.exists(self.pid))
        self.assertIn("stopping", self.read(self.log))


if __name__ == "__main__":
    unittest.main()
