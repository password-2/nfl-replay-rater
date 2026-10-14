"""Which upcoming games Sky Sports shows live in the UK, and which are likely
not live on DAZN.

Sky publishes a weekly article listing its live games. DAZN's UK rule: two
Sunday games a week are not live on DAZN Game Pass, one in the 6pm slot and
one in the 9pm slot, chosen by the UK broadcaster (Sky). Prime-time,
international, Thanksgiving and playoff games are always live on DAZN.

When Sky shows more than one game in a blackout slot, the one on the Sky
Sports NFL channel is marked as the likely blackout and the others as
uncertain.
"""
import html
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
ARTICLE_HINT = "live-on-sky-sports-fixtures-kick-off-times"
NEWS_INDEX = "https://www.skysports.com/nfl/news"
KNOWN_ARTICLE = ("https://www.skysports.com/nfl/news/12040/13582747/"
                 "nfl-2026-season-live-on-sky-sports-fixtures-kick-off-times-tv-channel-for-games-this-week-and-weekend")
CHANNELS = ["Sky Sports NFL", "Sky Sports Main Event", "Sky Sports+", "Sky Sports Football",
            "Sky Sports Mix", "Sky Sports Action", "Sky Sports Arena", "Sky Showcase", "Sky Sports Premier League"]
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Safari/605.1.15", "Accept-Language": "en-GB"}


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", "replace")


def text_lines(page):
    page = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page)
    page = re.sub(r"(?i)<(br|/p|/h\d|/li|/div|p|h\d|li)\b[^>]*>", "\n", page)
    page = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return [re.sub(r"\s+", " ", l).strip() for l in page.split("\n") if l.strip()]


def find_article():
    try:
        idx = _get(NEWS_INDEX)
        links = re.findall(r'href="([^"]*%s[^"]*)"' % ARTICLE_HINT, idx)
        if links:
            url = links[0]
            return url if url.startswith("http") else "https://www.skysports.com" + url
    except Exception:  # noqa: BLE001
        pass
    return KNOWN_ARTICLE


def parse_lines(lines, nicknames):
    """Return [(team_a, team_b, channels)] for lines that look like a fixture."""
    out = []
    for line in lines:
        if " @ " not in line and " vs " not in line.lower():
            continue
        if "KO" not in line and "kick" not in line.lower():
            continue
        found = []
        for nick in nicknames:
            m = re.search(r"\b%s\b" % re.escape(nick), line)
            if m:
                found.append((m.start(), nick))
        found.sort()
        if len(found) < 2:
            continue
        chans = [c for c in CHANNELS if c in line]
        out.append((found[0][1], found[1][1], chans))
    return out


def slot_of(date_iso):
    uk = datetime.fromisoformat(date_iso.replace("Z", "+00:00")).astimezone(UK)
    if uk.weekday() != 6:
        return None, uk
    if uk.hour in (17, 18):
        return "6pm", uk
    if uk.hour in (20, 21):
        return "9pm", uk
    return None, uk


def sky_games(games, now=None, fetch=True, lines=None):
    """games: current-season game rows. Returns {game_id: info} for upcoming games."""
    now = now or datetime.now(timezone.utc)
    if lines is None:
        lines = text_lines(_get(find_article())) if fetch else []
    nick = {}
    for g in games:
        for side in ("home", "away"):
            nick[g[side]["name"]] = g[side]["abbr"]
    fixtures = parse_lines(lines, sorted(nick, key=len, reverse=True))

    upcoming = [g for g in games if g.get("date") and
                now - timedelta(hours=6) < datetime.fromisoformat(g["date"].replace("Z", "+00:00")) < now + timedelta(days=10)]
    found = {}
    for a, b, chans in fixtures:
        ta, tb = nick[a], nick[b]
        for g in upcoming:
            if {g["home"]["abbr"], g["away"]["abbr"]} == {ta, tb}:
                found[g["id"]] = {"channels": chans, "game": g}
                break

    by_slot = {}
    for gid, info in found.items():
        slot, uk = slot_of(info["game"]["date"])
        info["slot"], info["uk"] = slot, uk
        if slot:
            by_slot.setdefault(slot, []).append(gid)

    out = {}
    for gid, info in found.items():
        g, slot = info["game"], info["slot"]
        if info["game"]["season_type"] != 2 or not slot:
            status = "dazn"           # on Sky and live on DAZN too
        else:
            peers = by_slot[slot]
            main = [p for p in peers if "Sky Sports NFL" in found[p]["channels"]]
            if len(peers) == 1 or (gid in main and len(main) == 1):
                status = "blackout"   # likely not live on DAZN
            else:
                status = "maybe"      # another Sky game in a blackout slot: unclear
        out[gid] = {
            "channels": info["channels"] or ["Sky Sports"],
            "status": status,
            "uk_ko": info["uk"].strftime("%a %H:%M"),
            "week": g["week"], "season_type": g["season_type"],
            "match": f'{g["away"]["abbr"]} @ {g["home"]["abbr"]}',
        }
    return out
