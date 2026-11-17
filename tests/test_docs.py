"""Fitness function for the docs: the README and the man page name every command and
config key the code has. Written after the 0.2.x README still described 0.1.0."""
import argparse
import io
import os
import re
import unittest
from contextlib import redirect_stdout

from test_jethrod import load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class DocsMatchCode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.j = load()
        cls.readme = read("README.md")
        cls.man = read("jethrod.1").replace("\\-", "-")
        out = io.StringIO()
        with redirect_stdout(out):
            try:
                cls.j.main(["--help"])
            except SystemExit:
                pass
        cls.commands = re.search(r"\{([a-z,-]+)\}", out.getvalue())
        cls.commands = cls.commands.group(1).split(",") if cls.commands else \
            re.findall(r"^    ([a-z][a-z-]+)\s", out.getvalue(), re.M)

    def test_found_the_commands(self):
        self.assertIn("run", self.commands)
        self.assertGreater(len(self.commands), 10)

    def test_every_command_is_documented(self):
        for cmd in self.commands:
            with self.subTest(cmd=cmd):
                in_readme = re.search(r"`(?:jethrod )?%s\b" % re.escape(cmd), self.readme)
                in_man = re.search(r"\b%s\b" % re.escape(cmd), self.man)
                self.assertTrue(in_readme, "README.md never shows `%s`" % cmd)
                self.assertTrue(in_man, "jethrod.1 never mentions %s" % cmd)

    def test_every_config_key_is_documented(self):
        for key in self.j.DEFAULTS:
            with self.subTest(key=key):
                self.assertTrue("`%s`" % key in self.readme, "README.md lacks `%s`" % key)
                self.assertTrue(re.search(r"\b%s\b" % key, self.man), "jethrod.1 lacks %s" % key)

    def test_changelog_has_this_version(self):
        self.assertIn("## [%s]" % self.j.VERSION, read("CHANGELOG.md"))


if __name__ == "__main__":
    unittest.main()
