"""Which upcoming games Sky Sports shows live in the UK, and which are likely
not live on DAZN.

Sky publishes a weekly article listing its live games. DAZN's UK rule: two
Sunday games a week are not live on DAZN Game Pass, one in the 6pm slot and
one in the 9pm slot, chosen by the UK broadcaster (Sky). Prime-time,
international, Thanksgiving and playoff games are always live on DAZN.

Rights rule: one Sky-exclusive game per 6pm and 9pm window is blacked out on
DAZN; Channel 5's games are never blacked out. So in each window, the
blackout is the Sky game that Channel 5 isn't showing (preferring Sky Sports
NFL if that still leaves more than one).

Channel 5's picks and DAZN's own status come from the weekly "where to watch
tracker" post on r/NFLUK when it can be read. Where that post gives a DAZN
status for a game, it wins over the rule.
"""
import html
import json
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


REDDIT_SEARCH = ("https://www.reddit.com/r/NFLUK/search.json?q=%22where%20to%20watch%20tracker%22"
                 "&restrict_sr=1&sort=new&limit=5")
YES = ("\u2705", ":white_check_mark:", "\u2714")
NO = ("\u274c", ":x:", "\u2716")


def fetch_tracker():
    """Markdown body of the newest r/NFLUK where-to-watch tracker, or ''."""
    for url in (REDDIT_SEARCH, REDDIT_SEARCH.replace("www.reddit", "old.reddit")):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "replay-rater/1.0 (personal NFL schedule helper)"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for child in data.get("data", {}).get("children", []):
                post = child.get("data", {})
                if "where to watch" in post.get("title", "").lower() and "|" in post.get("selftext", ""):
                    return post["selftext"]
        except Exception:  # noqa: BLE001
            continue
    return ""


def parse_tracker(md, nicknames):
    """[(nick_a, nick_b, {"ch5": str|None, "sky": str|None, "dazn": bool|None})]"""
    rows, header = [], None
    flag = lambda c: True if any(y in c for y in YES) else False if any(n in c for n in NO) else None
    for line in md.splitlines():
        if line.count("|") < 2:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        low = [c.lower() for c in cells]
        if any("dazn" in c for c in low) and any("sky" in c for c in low):
            header = {"ch5": next((i for i, c in enumerate(low) if "channel 5" in c or c in ("5", "five")), None),
                      "sky": next((i for i, c in enumerate(low) if "sky" in c), None),
                      "dazn": next((i for i, c in enumerate(low) if "dazn" in c), None)}
            continue
        if not header or set("".join(cells)) <= set("-: "):
            continue
        found = sorted((m.start(), n) for n in nicknames for m in [re.search(r"\b%s\b" % re.escape(n), line)] if m)
        if len(found) < 2:
            continue
        get = lambda k: cells[header[k]] if header.get(k) is not None and header[k] < len(cells) else ""
        info = {"ch5": None, "sky": None, "dazn": flag(get("dazn"))}
        c5, sk = get("ch5"), get("sky")
        if flag(c5):
            info["ch5"] = "5Action" if re.search(r"5\s*a", c5, re.I) else "Channel 5"
        if flag(sk):
            rest = re.sub(r"[^A-Za-z+ ]", " ", sk).strip()
            info["sky"] = "Sky Sports+" if "+" in sk else ("Sky Sports " + rest) if rest else "Sky Sports"
        rows.append((found[0][1], found[1][1], info))
    return rows


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


def sky_games(games, now=None, fetch=True, lines=None, tracker=None):
    """games: current-season game rows. Returns {game_id: info} for upcoming games."""
    now = now or datetime.now(timezone.utc)
    if lines is None:
        lines = text_lines(_get(find_article())) if fetch else []
    if tracker is None:
        tracker = fetch_tracker() if fetch else ""
    nick = {}
    for g in games:
        for side in ("home", "away"):
            nick[g[side]["name"]] = g[side]["abbr"]
    order = sorted(nick, key=len, reverse=True)

    upcoming = [g for g in games if g.get("date") and
                now - timedelta(hours=6) < datetime.fromisoformat(g["date"].replace("Z", "+00:00")) < now + timedelta(days=10)]

    def match(a, b):
        for g in upcoming:
            if {g["home"]["abbr"], g["away"]["abbr"]} == {nick[a], nick[b]}:
                return g
        return None

    found = {}
    for a, b, chans in parse_lines(lines, order):
        g = match(a, b)
        if g:
            found[g["id"]] = {"game": g, "sky": chans or ["Sky Sports"], "ch5": None, "dazn": None}
    tracker_rows = parse_tracker(tracker, order) if tracker else []
    for a, b, info in tracker_rows:
        g = match(a, b)
        if not g:
            continue
        f = found.setdefault(g["id"], {"game": g, "sky": [], "ch5": None, "dazn": None})
        f["ch5"], f["dazn"] = info["ch5"], info["dazn"]
        if not f["sky"] and info["sky"]:
            f["sky"] = [info["sky"]]
    have_ch5 = any(a for _, _, a in tracker_rows)

    by_slot = {}
    for gid, info in found.items():
        slot, uk = slot_of(info["game"]["date"])
        info["slot"], info["uk"] = slot, uk
        if slot and info["sky"]:
            by_slot.setdefault(slot, []).append(gid)

    out = {}
    for gid, info in found.items():
        g, slot = info["game"], info["slot"]
        source = "rule"
        if info["dazn"] is not None:
            status, source = ("dazn" if info["dazn"] else "blackout"), "tracker"
        elif not info["sky"] or g["season_type"] != 2 or not slot or info["ch5"]:
            status = "dazn"
        else:
            cands = [p for p in by_slot[slot] if not (have_ch5 and found[p]["ch5"])]
            main = [p for p in cands if "Sky Sports NFL" in found[p]["sky"]]
            if len(cands) == 1 or (len(main) == 1 and gid in main):
                status = "blackout"
            elif gid in cands:
                status = "maybe"
            else:
                status = "dazn"
        out[gid] = {
            "channels": info["sky"] + ([info["ch5"]] if info["ch5"] else []),
            "sky": bool(info["sky"]), "ch5": info["ch5"],
            "status": status, "source": source,
            "uk_ko": info["uk"].strftime("%a %H:%M"),
            "week": g["week"], "season_type": g["season_type"],
            "match": f'{g["away"]["abbr"]} @ {g["home"]["abbr"]}',
        }
    return out
