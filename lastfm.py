"""Last.fm, for concert watch suggestions (post-v1 #47): a user's public top artists (user.gettopartists), read with an
API key and no login. Suggestions only: nothing is stored but what the editor ticks (an artist's name). Takes no
Connection and never imports app. The key is a query parameter (the API's only way), so it is never logged: failures
raise ApiError(502, "Lookup failed") with no detail. Last.fm's terms ask for credit and links to Last.fm, which the
watch page shows."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import settings
from common import ApiError

URL = "https://ws.audioscrobbler.com/2.0/"
TIMEOUT = 10
MIN_GAP = 1.0  # well inside Last.fm's limits; the page asks once per visit
LIMIT = 50
PERIOD = "12month"
_lock = threading.Lock()
_last_call = None  # time.monotonic() of the last request, per process


def enabled() -> bool:
    return bool(settings.LASTFM_API_KEY and settings.LASTFM_USER)


def _fail():
    return ApiError(502, "Lookup failed")


def top_artists(user, limit=LIMIT) -> list[dict]:
    """[{"name", "playcount", "mbid" (or None), "url" (a last.fm link, or None)}], most played first; [] when the
    user has none. 502 "Lookup failed" on an HTTP error, a timeout, Last.fm's own error answer or an unreadable one."""
    global _last_call
    query = urllib.parse.urlencode({"method": "user.gettopartists", "user": user, "period": PERIOD, "limit": limit,
                                    "api_key": settings.LASTFM_API_KEY, "format": "json"})
    req = urllib.request.Request(f"{URL}?{query}", headers={"User-Agent": settings.UA, "Accept": "application/json"})
    with _lock:
        if _last_call is not None:
            wait = MIN_GAP - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
        _last_call = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(2_000_000)
        except (OSError, urllib.error.URLError, ValueError):  # HTTPError and timeouts are OSError
            raise _fail() from None
        finally:
            _last_call = time.monotonic()
    try:
        data = json.loads(body)
        if "error" in data:
            raise ValueError("error answer")
        found = (data.get("topartists") or {}).get("artist") or []
        if isinstance(found, dict):  # one artist comes back as an object, not a list
            found = [found]
        out = []
        for a in found:
            name = str(a["name"]).strip()
            if not name:
                continue
            url = a.get("url") if str(a.get("url") or "").startswith("https://www.last.fm/") else None
            out.append({"name": name, "playcount": int(a.get("playcount") or 0), "mbid": a.get("mbid") or None,
                        "url": url})
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _fail() from None
    return out
