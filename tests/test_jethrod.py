import copy
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "jethrod")
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
        self.j.notify = lambda cfg, t, b, log=print: self.sent.append((t, b)) or ["fake"]
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
        doc = json.load(open(FIXTURE))
        show = copy.deepcopy(doc["included"][0])
        show["id"] = "fake-us-show"
        show["attributes"].update({
            "formatted-address": "Saratoga Springs, NY",
            "venue-name": "Saratoga Performing Arts Center",
            "starts-at": "2027-08-01T00:00:00Z",
            "starts-at-date-local": "2027-07-31"})
        doc["included"].append(show)
        path = os.path.join(self.tmp.name, "feed.json")
        json.dump(doc, open(path, "w"))
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
        doc = json.load(open(cfg["feed"][7:]))
        doc["included"][-1]["attributes"]["formatted-address"] = "Lenox"
        json.dump(doc, open(cfg["feed"][7:], "w"))
        fresh, _ = self.j.check_once(cfg, log=lambda m: None)
        self.assertEqual(len(fresh), 1)
        self.assertTrue(fresh[0]["clock_hint"])

    def test_failed_delivery_retries_next_time(self):
        cfg = self._with_us_show()
        self.j.notify = lambda cfg, t, b, log=print: []
        self.j.check_once(cfg, log=lambda m: None)
        self.j.notify = lambda cfg, t, b, log=print: ["fake"]
        fresh, _ = self.j.check_once(cfg, log=lambda m: None)
        self.assertEqual(len(fresh), 1)


if __name__ == "__main__":
    unittest.main()
