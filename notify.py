"""Concert watch notifications (post-v1 #47): one push per run listing the new sightings, to WATCH_NOTIFY_URL in
WATCH_NOTIFY_FORMAT. Route-free; never imports app.

  ntfy  a plain-text body (one line per concert and its link) with Title and Click headers: an ntfy.sh or self-hosted
        ntfy topic URL
  json  {"title", "message", "items": [...]}: a webhook, such as a Home Assistant automation that calls a notify
        service

Only sightings whose notified_at is NULL are sent, and notified_at is set only after the push succeeded, so a re-run,
or a crash after sending, never repeats one, and a failed push is tried again next run. The URL is a secret like a key:
it is never logged and never in a response."""
import json
import logging
import urllib.error
import urllib.request

import settings

log = logging.getLogger(__name__)
TIMEOUT = 10
FORMATS = ("ntfy", "json")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def enabled() -> bool:
    return settings.WATCH_NOTIFY_URL.startswith(("https://", "http://"))


def fmt_day(d) -> str:
    """"2027-03-14" -> "Mar 14, 2027"; anything else as given."""
    try:
        y, m, day = (int(x) for x in str(d).split("-"))
        return f"{_MONTHS[m - 1]} {day}, {y}"
    except (ValueError, IndexError):
        return str(d)


def line(item) -> str:
    """"Marigold Static · Mar 14, 2027 · Old Mill Theatre, Portland"."""
    return " · ".join((item["artist"], fmt_day(item["date"]), f"{item['venue']}, {item['city']}"))


def message(items) -> "tuple[str, str]":
    """(title, text): one line per sighting, each followed by its link when it has one."""
    n = len(items)
    title = f"{settings.APP_NAME}: {n} new concert{'' if n == 1 else 's'}"
    lines = []
    for item in items:
        lines.append(line(item))
        if item.get("url"):
            lines.append(item["url"])
    return title, "\n".join(lines)


def _latin1(s):
    try:
        s.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


def payload(items, fmt) -> "tuple[bytes, dict]":
    """The request body and headers for this format (anything but "json" is ntfy)."""
    title, text = message(items)
    if fmt == "json":
        keep = ("artist", "date", "venue", "city", "region", "country", "url", "source")
        body = {"title": title, "message": text, "items": [{k: item.get(k) for k in keep} for item in items]}
        return json.dumps(body).encode(), {"Content-Type": "application/json"}
    headers = {"Content-Type": "text/plain; charset=utf-8",
               "Title": title if _latin1(title) else "New concerts"}
    click = next((i["url"] for i in items if i.get("url")), None)
    if click and click.isascii():
        headers["Click"] = click
    return text.encode(), headers


def push(items) -> bool:
    """POST the items to WATCH_NOTIFY_URL. False when there is no URL or the push failed (logged without the URL)."""
    if not items or not enabled():
        return False
    data, headers = payload(items, settings.WATCH_NOTIFY_FORMAT)
    req = urllib.request.Request(settings.WATCH_NOTIFY_URL, data=data, method="POST",
                                 headers={"User-Agent": settings.UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            resp.read(10_000)
    except urllib.error.HTTPError as e:
        log.warning("concert watch: the notification was refused (HTTP %s); it will be tried again", e.code)
        return False
    except (OSError, ValueError) as e:  # URLError, timeouts and refusals are OSError
        log.warning("concert watch: the notification could not be sent (%s); it will be tried again", type(e).__name__)
        return False
    return True


def notify_new(con, items) -> int:
    """Push these not-yet-notified sightings (dicts with "id", "artist", "date", "venue", "city", ...) and mark
    them notified. Returns how many were sent: 0 when there is no URL or the push failed (they stay unmarked)."""
    if not push(items):
        return 0
    con.executemany("UPDATE sighting SET notified_at = CURRENT_TIMESTAMP WHERE id = ? AND notified_at IS NULL",
                    [(i["id"],) for i in items])
    return len(items)
