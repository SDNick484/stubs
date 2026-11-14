"""No personal data in tracked files (spec sections 2 and 3). Walks `git ls-files` and fails on:

- an email address whose domain is not example.com (GitHub noreply addresses are allowed, for commit authors);
- a URL whose host is not on the ALLOWED list (except in static/vendor/, whose pinned upstream files cite their
  own bug trackers and may not be edited);
- the owner's hostname shape: `concerts.` on any domain but example.com, with or without a URL around it;
- a private or carrier-grade LAN address other than the documentation ones the tests use;
- a tracked *.csv outside tests/fixtures/ (the owner's real concert file must never be committed);
- any string in tests/hygiene_denylist.txt. That file is git-ignored and optional: keep real names, emails and
  hostnames in it locally, one per line, so they never enter the repo.

The patterns that would trip this file are built from fragments, so this file passes its own check."""
import ipaddress
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DENYLIST = os.path.join(ROOT, "tests", "hygiene_denylist.txt")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
URL = re.compile(r"https?://(?:[^\s/@:]*@)?([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)", re.I)
# The owner's site lives at a `concerts.` subdomain; the docs and tests say concerts.example.com.
OWNER_HOST = re.compile(r"\b" + "concerts" + r"\.(?!example\.com\b)[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b", re.I)
IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
# Hosts that are nobody's home: placeholders, the services the app talks to, and where its code and licences live.
# www.w3.org is the SVG namespace name static/icon.svg must carry (an identifier, never fetched).
ALLOWED = ("example.com", "example.org", "noreply.github.com", "github.com", "githubusercontent.com", "cloudflare.com",
           "cloudflareaccess.com", "openstreetmap.org", "setlist.fm", "schema.org", "www.w3.org", "leafletjs.com",
           "claude.com", "claude.ai", "127.0.0.1", "localhost")
# 192.168.1.100 is the spec's invented LAN host; 10.0.0.5, 169.254.169.254 and 0.0.0.0 are SSRF test targets.
ALLOWED_IPS = {"192.168.1.100", "10.0.0.5", "169.254.169.254", "127.0.0.1", "0.0.0.0"}
SSRF_HOSTS = {"0x7f.1"}  # loopback written in hex, a test target of the URL fetcher (Task 19)
DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
LAN_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")]
# Only used when git is unavailable (a source tarball): mirrors .gitignore closely enough for a test.
SKIP_DIRS = {".git", ".venv", "venv", "data", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
SKIP_FILES = {DENYLIST, os.path.join(ROOT, ".env"), os.path.join(ROOT, "PR_BODY.md")}


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        return [os.path.join(ROOT, p) for p in out.decode().split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            files += [p for p in (os.path.join(dirpath, f) for f in filenames) if p not in SKIP_FILES]
        return files


def allowed_domain(domain):
    domain = domain.lower()
    return any(domain == d or domain.endswith("." + d) for d in ALLOWED) or allowed_ip(domain) or domain in SSRF_HOSTS


def allowed_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    return str(ip) in ALLOWED_IPS or any(ip in n for n in DOC_NETS)


def lan_address(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False  # 300.1.2.3 or a version number with a leading zero: not an address
    if text.endswith((".0", ".255")):
        return False  # a network or broadcast address (the ranges in LAN_NETS above), never a host
    return not allowed_ip(text) and any(ip in n for n in LAN_NETS)


def denied_strings():
    if not os.path.exists(DENYLIST):
        return []
    with open(DENYLIST, encoding="utf-8") as f:
        return [s.strip().lower() for s in f if s.strip() and not s.lstrip().startswith("#")]


def find_problems(rel, body, denied=()):
    problems = [f"{rel}: email {m.group(0)}" for m in EMAIL.finditer(body)
                if not (m.group(1).lower() == "example.com" or m.group(1).lower().endswith(".example.com")
                        or m.group(1).lower() == "users.noreply.github.com")]
    if not rel.replace(os.sep, "/").startswith("static/vendor/"):
        problems += [f"{rel}: url host {m.group(1)}" for m in URL.finditer(body) if not allowed_domain(m.group(1))]
    problems += [f"{rel}: owner hostname {m.group(0)}" for m in OWNER_HOST.finditer(body)]
    problems += [f"{rel}: LAN address {m.group(1)}" for m in IPV4.finditer(body) if lan_address(m.group(1))]
    low = body.lower()
    return problems + [f"{rel}: a denylisted string" for s in denied if s in low]


def test_no_personal_identifiers():
    files = tracked_files()
    assert files, "found no files to check"
    denied = denied_strings()
    problems = []
    for path in files:
        if not os.path.isfile(path) or path in SKIP_FILES:
            continue  # deleted in the working tree but still in the index
        with open(path, "rb") as f:
            body = f.read().decode("utf-8", errors="ignore")
        problems += find_problems(os.path.relpath(path, ROOT), body, denied)
    assert not problems, "\n".join(problems)


def test_no_csv_outside_fixtures():
    bad = [os.path.relpath(p, ROOT) for p in tracked_files() if p.lower().endswith(".csv")
           and not os.path.relpath(p, ROOT).replace(os.sep, "/").startswith("tests/fixtures/")]
    assert not bad, f"CSV files belong only in tests/fixtures/: {bad}"


def test_csv_is_ignored_outside_fixtures():
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
        lines = [s.strip() for s in f]
    assert "*.csv" in lines and "!tests/fixtures/*.csv" in lines
    assert "data/" in lines and "dist/" in lines and ".env" in lines and "tests/hygiene_denylist.txt" in lines


def test_the_check_catches_what_it_should():
    real = "family" + "-name.net"  # built from parts so this file does not trip the check it tests
    assert find_problems("x", "mail jo@" + real)
    assert find_problems("x", "see https://nas." + real + "/x")
    assert find_problems("x", "served at " + "concerts" + "." + real)
    assert find_problems("x", "Alex lives here", denied=["alex"])
    assert not find_problems("static/vendor/lib/x.css", "see https:" + "//bugs.lib-upstream.org/1")
    assert find_problems("static/vendor/lib/x.css", "served at " + "concerts" + "." + real)
    assert find_problems("x", "ssh root@" + "192.168." + "7.7")
    assert find_problems("x", "http:" + "//10.1" + ".2.3:8080/")
    assert find_problems("x", "tailnet " + "100.101" + ".1.2")
    assert not find_problems("x", "a@example.com https://concerts.example.com/ https://github.com/SDNick484/stubs "
                                  "SDNick484@users.noreply.github.com http://192.168.1.100:8773 http://127.0.0.1:8773 "
                                  "https://tile.openstreetmap.org/{z}/{x}/{y}.png https://api.setlist.fm/rest/1.0 "
                                  "shellcheck-py==0.11.0.1 Leaflet 1.9.4 tests/fixtures/concerts.csv")
