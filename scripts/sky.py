"""DAZN blackouts in the UK, from Sky's own NFL listings.

Rule (Adam's, matching DAZN's help page): on Sunday, the game Sky shows on
Sky Sports NFL in the 6pm window and the one in the 9pm window (21:05/21:25)
are not live on DAZN Game Pass. Everything else is.

Source: https://www.skysports.com/watch/nfl-on-sky (listings, ~10 days ahead).
Check: https://www.dazn.com/en-GB/help/articles/16858954355485-... (the rule
itself). If that page's wording about the windows changes, we flag it.
"""
import hashlib
import html
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
SKY_URL = "https://www.skysports.com/watch/nfl-on-sky"
DAZN_URL = ("https://www.dazn.com/en-GB/help/articles/"
            "16858954355485-nfl-game-pass-blackout-restrictions-in-the-uk-and-republic-of-ireland")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Safari/605.1.15", "Accept-Language": "en-GB"}


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", "replace")


def text_lines(page):
    page = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page)
    page = re.sub(r"(?i)<(br|/p|/h\d|/li|/div|p|h\d|li|div)\b[^>]*>", "\n", page)
    page = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return [re.sub(r"\s+", " ", l).strip() for l in page.split("\n") if l.strip()]


def parse_listings(lines, nicknames):
    """[(nick_a, nick_b, on_sky_sports_nfl)] from Sky's listings page.

    Each game is a matchup line ("Away @ Home") followed, on the same or the
    next few lines, by "NFL - Week N, <channel> (HH:MM), ...".
    """
    out = []
    for i, line in enumerate(lines):
        if "@" not in line and not re.search(r"\bvs?\b", line):
            continue
        found = sorted((m.start(), n) for n in nicknames for m in [re.search(r"\b%s\b" % re.escape(n), line)] if m)
        if len(found) < 2:
            continue
        detail = " ".join(lines[i:i + 4])
        if "Sky Sports" not in detail:
            continue
        nxt = next((j for j in range(i + 1, min(i + 4, len(lines)))
                    if "@" in lines[j] and sum(bool(re.search(r"\b%s\b" % re.escape(n), lines[j])) for n in nicknames) >= 2), None)
        if nxt is not None:
            detail = " ".join(lines[i:nxt])
        out.append((found[0][1], found[1][1], "Sky Sports NFL" in detail))
    return out


def window(date_iso):
    uk = datetime.fromisoformat(date_iso.replace("Z", "+00:00")).astimezone(UK)
    if uk.weekday() == 6 and uk.hour in (17, 18):
        return "18:00", uk
    if uk.weekday() == 6 and uk.hour in (20, 21):
        return "21:xx", uk
    return None, uk


def blackouts(games, now=None, lines=None):
    """Returns (blackouts {game_id: info}, problems [str]) for the next ~10 days."""
    now = now or datetime.now(timezone.utc)
    if lines is None:
        lines = text_lines(_get(SKY_URL))
    nick = {}
    for g in games:
        for side in ("home", "away"):
            nick[g[side]["name"]] = g[side]["abbr"]
    listed = parse_listings(lines, sorted(nick, key=len, reverse=True))

    upcoming = [g for g in games if g.get("date") and g["season_type"] == 2 and
                now - timedelta(hours=6) < datetime.fromisoformat(g["date"].replace("Z", "+00:00")) < now + timedelta(days=10)]
    out, slots = {}, {}
    for a, b, on_nfl in listed:
        if not on_nfl:
            continue
        g = next((g for g in upcoming if {g["home"]["abbr"], g["away"]["abbr"]} == {nick[a], nick[b]}), None)
        if not g:
            continue
        win, uk = window(g["date"])
        if not win:
            continue
        out[g["id"]] = {"match": f'{g["away"]["abbr"]} @ {g["home"]["abbr"]}', "uk_ko": uk.strftime("%a %H:%M"),
                        "window": win, "week": g["week"], "season_type": g["season_type"]}
        slots.setdefault((g["week"], win), []).append(g["id"])

    problems = []
    weeks = {g["week"] for g in upcoming if window(g["date"])[0]}
    for wk in sorted(weeks):
        for win in ("18:00", "21:xx"):
            n = len(slots.get((wk, win), []))
            if n > 1:
                problems.append(f"Week {wk}: {n} Sky Sports NFL games in the {win} window")
    return out, problems, len(listed)


def dazn_rule_fingerprint():
    """Hash of the DAZN help page's wording about the blackout windows, or None."""
    try:
        lines = text_lines(_get(DAZN_URL))
    except Exception:  # noqa: BLE001
        return None, None
    key = [l for l in lines if re.search(r"black|18:00|21:00|6pm|9pm|Wednesday|two", l, re.I)]
    if not any(re.search(r"black", l, re.I) for l in key):
        return None, None  # page content not served to us (built in the browser): unknown, not a failure
    ok = any("18:00" in l or "6pm" in l.lower() for l in key) and any("21:00" in l or "9pm" in l.lower() for l in key)
    return hashlib.sha1("\n".join(key).encode()).hexdigest()[:12], ok
