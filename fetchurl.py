"""Fetch from a URL (spec 7.12): an editor pastes a link on a show; the server reads the page's og:image, og:title and
schema.org Event/MusicEvent JSON-LD and offers suggestions (use this image as the poster, add these performers, the
date says ...). Nothing is written until the editor applies one, and each applied suggestion records the URL.

The SSRF guard. The server fetches addresses a person typed, so every hop of every fetch goes through check_host
before any connection is made: the host is resolved once, every address it resolves to must be public (loopback,
private, shared, link-local, multicast, reserved, unspecified and documentation addresses are refused, and an IPv4
address carried inside an IPv6 one, as with ::ffff:127.0.0.1, 6to4, Teredo or NAT64, is checked as itself), and the
socket is then opened to one of those checked addresses, never to the name again, so DNS can't be switched between
the check and the connection. A host whose last label is a number (2130706433, 0x7f.1, 127.1, 0177.0.0.1) is an
address in disguise and is refused unless it is a plain dotted quad that passes. Redirects are followed by hand, at
most five, each re-checked the same way. No proxy is used (http.client, not urllib's opener). Takes a
sqlite3.Connection and never imports app."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import unicodedata
from contextlib import contextmanager
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

import images
import settings
from common import ApiError, norm, parse_date
from db import transaction
from records import add_lineup_entry, add_source, get_or_create_artist, resolve_artist

MAX_PAGE_BYTES = 2_000_000
TIMEOUT = 5            # seconds, for each connect and each read
TOTAL_SECONDS = 20     # the whole fetch, redirects included, so a page can't trickle in forever
MAX_REDIRECTS = 5
MAX_URL = images.MAX_URL
MAX_NAME = 200
MAX_PERFORMERS = 50
KINDS = ("poster", "performer", "source")
REDIRECTS = (301, 302, 303, 307, 308)
HTML_TYPES = ("text/html", "application/xhtml+xml", "")
EVENT_TYPES = ("Event", "MusicEvent")
NOT_ALLOWED = "That address isn't allowed."

# A host whose last label is a number in any inet_aton spelling (decimal, octal 0177, hex 0x7f): an IP address.
_NUMERIC_LABEL = re.compile(r"(?:0x[0-9a-f]*|[0-9]+)", re.I)
_CONTROL = re.compile(r"[\x00-\x20\x7f]")
_NAT64 = [ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48")]
_IPV4_COMPAT = ipaddress.ip_network("::/96")
# Refused even if a Python version's is_global said otherwise: shared (carrier-grade NAT) and benchmarking space.
_ALSO_REFUSED = [ipaddress.ip_network(n) for n in ("100.64.0.0/10", "198.18.0.0/15", "192.0.0.0/24")]


# ---------------------------------------------------------------- the guard
def _embedded(ip):
    """IPv4 addresses an IPv6 address carries (mapped, IPv4-compatible, 6to4, Teredo, NAT64)."""
    if ip.version != 6:
        return []
    out = []
    if ip.ipv4_mapped:
        out.append(ip.ipv4_mapped)
    if ip.sixtofour:
        out.append(ip.sixtofour)
    if ip.teredo:
        out.extend(ip.teredo)
    if ip in _IPV4_COMPAT or any(ip in n for n in _NAT64):
        out.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return out


def _public(ip):
    """True only for an address that is globally reachable and nothing else: not loopback, private, link-local,
    multicast, reserved, unspecified, site-local, shared or documentation, and carrying no such IPv4 address."""
    if any(not _public(inner) for inner in _embedded(ip)):
        return False
    if any(ip in n for n in _ALSO_REFUSED if n.version == ip.version):
        return False
    if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or (ip.version == 6 and ip.is_site_local)):
        return False
    return ip.is_global


def _literal(host):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def check_host(host) -> list:
    """The addresses `host` resolves to, each checked: ApiError(400, "That address isn't allowed.") if any is not a
    public address. An address literal is checked as written; a name is resolved once, here. The caller connects
    only to the addresses returned (so this returns them, where the plan's contract said None)."""
    host = (host or "").strip()
    if not host:
        raise ApiError(400, NOT_ALLOWED)
    ip = _literal(host)
    if ip is None:
        labels = unicodedata.normalize("NFKC", host).rstrip(".").split(".")  # NFKC: fullwidth digits too
        if _NUMERIC_LABEL.fullmatch(labels[-1]):
            raise ApiError(400, NOT_ALLOWED)  # 2130706433, 0x7f.1, 127.1, 0177.0.0.1: an address in disguise
        try:
            infos = socket.getaddrinfo(host, None, 0, socket.SOCK_STREAM)
        except (OSError, UnicodeError, ValueError):
            raise ApiError(400, "Couldn't find that site.") from None
        found = []
        for info in infos:
            parsed = _literal(str(info[4][0]).split("%")[0])
            if parsed is None:
                raise ApiError(400, NOT_ALLOWED)
            found.append(parsed)
        if not found:
            raise ApiError(400, "Couldn't find that site.")
    else:
        found = [ip]
    if not all(_public(a) for a in found):
        raise ApiError(400, NOT_ALLOWED)
    return list(dict.fromkeys(str(a) for a in found))


# ---------------------------------------------------------------- connections pinned to a checked address
_tls_context = None


def _tls():
    global _tls_context
    if _tls_context is None:
        _tls_context = ssl.create_default_context()
    return _tls_context


class _PinnedHTTP(http.client.HTTPConnection):
    """An HTTP connection for `host` whose socket goes to `addr`, an address check_host passed. The name is not looked
    up again (a second lookup is what DNS rebinding exploits); the Host header still names the host."""

    def __init__(self, host, port, addr, timeout):
        super().__init__(host, port, timeout=timeout)
        self.addr = addr

    def connect(self):
        self.sock = socket.create_connection((self.addr, self.port), self.timeout)


class _PinnedHTTPS(_PinnedHTTP):
    """The same over TLS: the certificate is verified against the host name (and SNI sends it)."""
    default_port = 443

    def connect(self):
        super().connect()
        self.sock = _tls().wrap_socket(self.sock, server_hostname=self.host)


@contextmanager
def _open(scheme, host, port, addr, target):
    """One GET of `target` from `host`, connected to `addr`; yields the http.client response. The only place a
    connection is made (tests replace it)."""
    conn = (_PinnedHTTPS if scheme == "https" else _PinnedHTTP)(host, port, addr, TIMEOUT)
    try:
        conn.request("GET", target, headers={
            "User-Agent": settings.UA, "Accept": "text/html,application/xhtml+xml,image/*;q=0.9,*/*;q=0.5",
            "Accept-Encoding": "identity", "Connection": "close"})
        resp = conn.getresponse()
        try:
            yield resp
        finally:
            resp.close()
    finally:
        conn.close()


# ---------------------------------------------------------------- fetching
def _parse(url):
    """(scheme, host, port, target) of an http(s) URL; 400 for anything else."""
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL or _CONTROL.search(url.strip()):
        raise ApiError(400, "That isn't a web address.")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise ApiError(400, "That isn't a web address.") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ApiError(400, "Only http and https links can be fetched.")
    if not parts.hostname:
        raise ApiError(400, "That isn't a web address.")
    if parts.username is not None or parts.password is not None:
        raise ApiError(400, "Links with a user name or password aren't fetched.")
    target = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        target += "?" + quote(parts.query, safe="/%:@!$&'()*+,;=-._~?")
    return scheme, parts.hostname, port or (443 if scheme == "https" else 80), target


def _read(resp, max_bytes, deadline):
    """(status, location, content_type, body) of one response; the body is read only for a 2xx."""
    status = resp.status
    if status in REDIRECTS:
        return status, resp.getheader("Location"), "", b""
    if not 200 <= status < 300:
        raise ApiError(502, f"That site answered {status}.")
    length = (resp.getheader("Content-Length") or "").strip()
    if length.isdigit() and int(length) > max_bytes:
        raise ApiError(400, "Page too large.")
    chunks, total = [], 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("the whole fetch took too long")
        chunk = resp.read(min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ApiError(400, "Page too large.")
    ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
    return status, None, ctype, b"".join(chunks)


def _one_hop(url, max_bytes, deadline):
    scheme, host, port, target = _parse(url)
    addrs = check_host(host)
    for i, addr in enumerate(addrs):
        try:
            with _open(scheme, host, port, addr, target) as resp:
                return _read(resp, max_bytes, deadline)
        except TimeoutError:
            raise ApiError(502, "That site took too long to answer.") from None
        except http.client.HTTPException:
            raise ApiError(502, "That site's answer couldn't be read.") from None
        except OSError:
            if i == len(addrs) - 1:
                raise ApiError(502, "Couldn't reach that site.") from None
    raise ApiError(502, "Couldn't reach that site.")  # not reached: check_host returns at least one address


def safe_fetch(url, max_bytes=MAX_PAGE_BYTES) -> tuple:
    """(final_url, body, content_type) for an http(s) URL. Every hop goes through check_host and connects to the
    address it checked; at most MAX_REDIRECTS redirects; TIMEOUT seconds per network wait, TOTAL_SECONDS in all.
    400 for a refused or malformed address or a body over max_bytes ("Page too large."); 502 when the site errs,
    times out or can't be reached. content_type is the bare media type, lowercased ("text/html")."""
    deadline = time.monotonic() + TOTAL_SECONDS
    for _ in range(MAX_REDIRECTS + 1):
        status, location, ctype, body = _one_hop(url, max_bytes, deadline)
        if status not in REDIRECTS:
            return url, body, ctype
        if not location or not location.strip():
            raise ApiError(502, "That site redirected to nowhere.")
        url = urljoin(url, location.strip())
    raise ApiError(502, "Too many redirects.")


# ---------------------------------------------------------------- reading a page
def _clean(value, limit=MAX_NAME):
    """Whitespace collapsed; None when empty, not text, or longer than limit."""
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value if value and len(value) <= limit else None


def _absolute(base_url, value):
    """An http(s) address for `value` read against the page, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    url = urljoin(base_url, value.strip())
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or len(url) > MAX_URL:
        return None
    return None if _CONTROL.search(url) else url


class _Reader(HTMLParser):
    """Collects the first og:image, og:title and <title>, and the text of every application/ld+json script."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.title, self.ld = {}, None, []
        self._title, self._script = None, None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").strip().lower()
            if key in ("og:image", "og:image:url", "og:image:secure_url", "og:title"):
                self.meta.setdefault(key, a.get("content", ""))
        elif tag == "title" and self.title is None and self._title is None:
            self._title = []
        elif tag == "script" and a.get("type", "").split(";")[0].strip().lower() == "application/ld+json":
            self._script = []

    def handle_endtag(self, tag):
        if tag == "title" and self._title is not None:
            self.title, self._title = "".join(self._title), None
        elif tag == "script" and self._script is not None:
            self.ld.append("".join(self._script))
            self._script = None

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        elif self._title is not None:
            self._title.append(data)


def _type_names(node):
    types = node.get("@type")
    types = types if isinstance(types, list) else [types]
    return {t.rsplit("/", 1)[-1].rsplit(":", 1)[-1] for t in types if isinstance(t, str)}


def _events(node, depth=0):
    """Event/MusicEvent objects in a JSON-LD document: at the top, in a list, in @graph or as mainEntity."""
    if depth > 8:
        return
    if isinstance(node, list):
        for item in node:
            yield from _events(item, depth + 1)
    elif isinstance(node, dict):
        if _type_names(node) & set(EVENT_TYPES):
            yield node
        for key in ("@graph", "mainEntity"):
            if key in node:
                yield from _events(node[key], depth + 1)


def _name_of(value):
    if isinstance(value, dict):
        return _clean(value.get("name"))
    return _clean(value)


def _first_name(value):
    for item in (value if isinstance(value, list) else [value]):
        name = _name_of(item)
        if name:
            return name
    return None


def _image_of(value, base_url):
    for item in (value if isinstance(value, list) else [value]):
        if isinstance(item, dict):
            item = item.get("url") or item.get("contentUrl")
        url = _absolute(base_url, item)
        if url:
            return url
    return None


def _date_of(value):
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", value) if isinstance(value, str) else None
    if not m:
        return None
    try:
        return parse_date(m.group(1))[0]
    except ValueError:
        return None


def extract(html: str, base_url: str) -> dict:
    """{"image": abs_url|None, "title": str|None, "event": {"date": "YYYY-MM-DD"|None, "venue": str|None,
    "performers": [str]}} from og:image, og:title (else <title>) and the first JSON-LD Event or MusicEvent
    (startDate, location, performer; its image when the page has no og:image). Performers are de-duplicated by
    common.norm. Only http(s) image addresses are kept."""
    reader = _Reader()
    try:
        reader.feed(html or "")
        reader.close()
    except (AssertionError, ValueError):
        pass  # malformed markup: keep what was read before it
    event = None
    for block in reader.ld:
        try:
            doc = json.loads(block)
        except ValueError:
            continue
        event = next(_events(doc), None)
        if event is not None:
            break
    image = next((u for u in (_absolute(base_url, reader.meta.get(k)) for k in
                              ("og:image", "og:image:secure_url", "og:image:url")) if u), None)
    out = {"image": image, "title": _clean(reader.meta.get("og:title"), 300) or _clean(reader.title, 300),
           "event": {"date": None, "venue": None, "performers": []}}
    if event is not None:
        performers, seen = [], set()
        raw = event.get("performer", event.get("performers"))
        for item in (raw if isinstance(raw, list) else [raw]):
            name = _name_of(item)
            if name and norm(name) not in seen and len(performers) < MAX_PERFORMERS:
                seen.add(norm(name))
                performers.append(name)
        out["event"] = {"date": _date_of(event.get("startDate")), "venue": _first_name(event.get("location")),
                        "performers": performers}
        out["image"] = out["image"] or _image_of(event.get("image"), base_url)
    return out


def _decode(body):
    """Page bytes as text: a BOM or a <meta charset> wins, else UTF-8, else Windows-1252 (as csvimport does)."""
    for bom, codec in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if body.startswith(bom):
            return body[len(bom):].decode(codec, errors="replace")
    m = re.search(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", body[:4096], re.I)
    if m:
        try:
            return body.decode(m.group(1).decode("ascii"), errors="replace")
        except LookupError:
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


# ---------------------------------------------------------------- suggestions and apply
def _show(con, show_id):
    row = con.execute("SELECT id, date FROM show WHERE id = ?", (show_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Not found")
    return row


def suggestions(con, show_id, url) -> dict:
    """What the page at `url` offers this show: extract()'s answer with "url" (the page's address after
    redirects, which apply records), "performers" as [{"name", "on_lineup"}] and "date_differs". A bare image
    link is offered as the poster. Writes nothing."""
    show = _show(con, show_id)
    final, body, ctype = safe_fetch(url)
    if images.sniff_image(body):
        data = {"image": final, "title": None, "event": {"date": None, "venue": None, "performers": []}}
    elif ctype in HTML_TYPES:
        data = extract(_decode(body), final)
    else:
        raise ApiError(400, "That link isn't a web page or an image.")
    on_lineup = {r["artist_id"] for r in con.execute("SELECT artist_id FROM lineup WHERE show_id = ?", (show_id,))}
    performers = [{"name": n, "on_lineup": resolve_artist(con, n) in on_lineup} for n in data["event"]["performers"]]
    date = data["event"]["date"]
    return {"url": final, **data, "performers": performers, "date_differs": bool(date and date != show["date"])}


def _page_url(url):
    if not isinstance(url, str) or len(url) > MAX_URL or _absolute(url, url) != url:
        raise ApiError(400, "url must be an http or https address.")
    return url


def _label(url):
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def apply(con, show_id, kind, value, url) -> dict:
    """Apply one suggestion, recording `url` (the page) as its source.
    poster:    fetch `value` (an image address) through safe_fetch and save it as the show's poster, source_url = url;
    performer: put the artist named `value` on the lineup as a performer, with a source backing that entry (the
               show's version is bumped, so an edit form opened before is refused as stale);
    source:    add url as a source of the show itself, labelled `value` or the site's host name (409 if it is one).
    Returns {"image": {...}}, {"artist_id", "lineup_id", "source_id"} or {"source_id"}."""
    if kind not in KINDS:
        raise ApiError(400, "kind must be poster, performer or source.")
    url = _page_url(url)
    _show(con, show_id)
    if kind == "poster":
        if not isinstance(value, str) or not value.strip():
            raise ApiError(400, "value must be the image's address.")
        _, body, _ = safe_fetch(value, max_bytes=images.MAX_IMAGE_BYTES)
        iid = images.save_image(con, show_id, "poster", body, source_url=url)
        return {"image": images.get_image(con, iid)}
    if value is not None and not isinstance(value, str):
        raise ApiError(400, "value must be text.")
    value = " ".join((value or "").split())
    if len(value) > MAX_NAME:
        raise ApiError(400, f"value must be at most {MAX_NAME} characters.")
    with transaction(con):
        if kind == "performer":
            artist_id = get_or_create_artist(con, value)
            lineup_id = add_lineup_entry(con, show_id, artist_id, "performer")
            source_id = add_source(con, show_id, _label(url), url, lineup_id=lineup_id)
            con.execute("UPDATE show SET version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (show_id,))
            return {"artist_id": artist_id, "lineup_id": lineup_id, "source_id": source_id}
        if con.execute("SELECT 1 FROM source WHERE show_id = ? AND lineup_id IS NULL AND url = ?",
                       (show_id, url)).fetchone():
            raise ApiError(409, "That page is already a source.")
        return {"source_id": add_source(con, show_id, value or _label(url), url)}
