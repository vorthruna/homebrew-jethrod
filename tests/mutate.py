"""Mutation check: re-inject each bug class into a scratch copy; the test suite must
fail every time. Run: python tests/mutate.py"""
import os, shutil, subprocess, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUTANTS = {
    "Notepad BOM breaks config": ('encoding="utf-8-sig") as fh:  # -sig', 'encoding="utf-8") as fh:  # -sig'),
    "inline comments kept": ('return v[:m.start()].rstrip() if m else v', 'return v'),
    "ISO code = state (Bonn, DE)": ('if code in COLLIDING_CODES and city_region != reg:', 'if False:'),
    "foreign country ignored": ('        if q in FOREIGN:\n            return None, "foreign"\n', ''),
    "namesake cities trusted": ('("maybe" if city in DUP_CITIES else "sure")', '"sure"'),
    "showtime can't veto maybe": ('if conf == "maybe" and verdict == "other":', 'if False:'),
    "24:00 digest never runs": ('if 0 <= hh <= 23 and 0 <= mm <= 59 else (8, 0)', 'if True else (8, 0)'),
    "flag ignores whitespace": ('v = str(value if value is not None else "").strip().lower()',
                                'v = str(value if value is not None else "").lower()'),
    "interval=inf never polls": ('if not math.isfinite(minutes):', 'if False:'),
    "undelivered alert marked done": ('        if channels:\n            for e in fresh:', '        if True:\n            for e in fresh:'),
    "older edit reported": ('elif item["modified"] > known[key]:', 'elif item["modified"] != known[key]:'),
    "outage -> digest storm": ('fresh_types = set(ok) - seen_types', 'fresh_types = set()'),
    "ntfy priority wrong": ('{"high": 4, "default": 3}', '{"high": 3, "default": 3}'),
    "ntfy topic not stripped": ('topic = cfg.get("ntfy_topic", "").strip()', 'topic = cfg.get("ntfy_topic", "")'),
    "padding breaks addresses": ('re.sub(r"\\s+", " ", (s or "").strip().lower())', '(s or "").lower()'),
    "no state lock (double alert)": ("                PidFile.lock(fd)\n                break", "                break"),
    "bare foreign city = NA": ('    if city in FOREIGN_CITIES:\n        return None, "foreign"\n', ''),
    "AppleScript \\u escapes": ("applescript_string(body), applescript_string(title))", "json.dumps(body), json.dumps(title))"),
    "CLI traceback when offline": ("except (OSError, ValueError, RuntimeError) as exc:  # offline", "except ZeroDivisionError as exc:  # offline"),
    "pythonw flush crash": ("        if sys.stdout is not None:  # pythonw has no stdout\n            sys.stdout.flush()", "        if True:\n            sys.stdout.flush()"),
    "state missing keys": ('    if not isinstance(state.get("alerted"), dict):', '    if False:'),
    "site setting ignored": ('                         site=(cfg.get("site") or SITE).strip())', '                         site=SITE)'),
    "USR1 does nothing": ('        self._on("SIGUSR1", kick)', '        pass'),
    "HUP keeps old config": ('                    cfg = load_config()\n                    LOG.info("reloaded', '                    LOG.info("reloaded'),
    "log says none in NA": ('        if na:\n            log(', '        if False:\n            log('),
}
survivors = []
for name, (old, new) in MUTANTS.items():
    tmp = tempfile.mkdtemp()
    shutil.copytree(SRC, os.path.join(tmp, "r"), ignore=shutil.ignore_patterns(".git"))
    path = os.path.join(tmp, "r", "jethrod.py")
    s = open(path, encoding="utf-8").read()
    assert s.count(old) == 1, "mutant %r: anchor found %d times" % (name, s.count(old))
    open(path, "w", encoding="utf-8").write(s.replace(old, new))
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                       cwd=os.path.join(tmp, "r"),
                       capture_output=True, text=True)
    killed = r.returncode != 0
    print("%-32s %s" % (name, "KILLED" if killed else "SURVIVED  <---"))
    if not killed:
        survivors.append(name)
    shutil.rmtree(tmp)
print("\n%d/%d mutants killed" % (len(MUTANTS) - len(survivors), len(MUTANTS)))
sys.exit(1 if survivors else 0)
