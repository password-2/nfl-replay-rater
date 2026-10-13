#!/usr/bin/env python3
"""NFL Game Rater build script.

Fetches the current NFL season from ESPN's public feed, rates every finished
game for entertainment value, writes docs/data.json for the web page, and
sends a phone notification (ntfy) when a round of games has finished.

Runs on GitHub Actions. No third-party packages needed.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stakes import season_stakes  # noqa: E402

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# QUALITY: points each factor is worth at full strength (factor score 1.0),
# plus a base every game gets. Factors cap at 1.25, so quality can pass 100.
# Starting weights; to be refitted to Adam's own verdicts.
BASE_POINTS = 10
WEIGHTS = {
    "style": 26,          # Shootout or defensive battle, whichever is stronger
    "nail_biter": 14,     # Share of the second half played within one score
    "lead_changes": 10,   # Lead changes and ties
    "upset": 10,          # Underdog won (betting line, else form)
    "close": 8,           # Final margin, overtime
    "score_to_win": 8,    # How late the winner took the lead for good
    "comeback": 8,        # Biggest deficit the winner overcame
    "big_plays": 8,       # Touchdowns of 40+ yards
    "defence_st": 8,      # Defensive / special teams scores and safeties
    "chaos": 6,           # Fights, ejections, taunting and other fun penalties
}

# Published on the page's "How it works" panel.
FACTOR_INFO = {
    "style": ("Shootout or defensive battle",
              "Counts whichever is stronger. Shootout: full at 54 total points. Defensive battle: full when the game "
              "stays within a score with 20 or fewer total points; turnovers and sacks add a little."),
    "nail_biter": ("Nail-biter", "Share of the second half played within 8 points. Q4 and overtime count double."),
    "lead_changes": ("Lead changes", "Full at 4 lead changes. Each tie counts as half."),
    "upset": ("Upset", "Full when a 9-point underdog wins, using the betting line. Without a line it uses form "
              "(point margin so far). A tie by the underdog counts half."),
    "close": ("Close finish", "Full for a tie or 1-point margin, falling to zero at 24. Overtime adds 25%."),
    "score_to_win": ("Late winning score", "Full when the winner went ahead for good in the last 2 minutes; less "
                     "the earlier it happened. Overtime counts 125%."),
    "comeback": ("Comeback", "Full when the winner came back from 14 points down."),
    "big_plays": ("Big plays", "Full at two touchdowns of 40+ yards."),
    "defence_st": ("Defence / special teams scores", "Full at 1.5 defensive or return touchdowns or safeties."),
    "chaos": ("Chaos", "Fights and ejections count most, then taunting and unsportsmanlike conduct, then personal "
              "fouls. Full at about two ejections. Only what appears in the play-by-play is caught."),
}
CAP = 1.25

TIERS = [  # (minimum quality, key, short label)
    (100, "now", "WATCH THIS NOW"),
    (80, "full", "Full game (2h)"),
    (60, "condensed", "Condensed (40m)"),
    (0, "highlights", "Highlights (5m)"),
]
TIER_KEYS = [t[1] for t in TIERS]          # best first
TIER_LABEL = {t[1]: t[2] for t in TIERS}

# STAKES (0-100, measured before kickoff) moves the verdict one tier.
STAKES_HIGH = 60   # at or above: up one tier
STAKES_DEAD = 8    # below: down one tier (nothing on the line)
STAKES_VERSION = 2
EXTRAS_VERSION = 3


def tier_for(quality):
    for floor, key, label in TIERS:
        if quality >= floor:
            return key, label
    return TIERS[-1][1], TIERS[-1][2]


def verdict(quality, stakes):
    """Returns (base tier, final tier, shift). shift is +1, 0 or -1."""
    base, _ = tier_for(quality)
    shift = 0
    if stakes is not None:
        if stakes >= STAKES_HIGH:
            shift = 1
        elif stakes < STAKES_DEAD:
            shift = -1
    i = TIER_KEYS.index(base)
    j = min(len(TIER_KEYS) - 1, max(0, i - shift))
    return base, TIER_KEYS[j], (i - j)


# ---------------------------------------------------------------- fetching

def get(url, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nfl-game-rater/1.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001
            if attempt == tries - 1:
                raise
            print(f"retry {url}: {exc}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))


def team_info(c):
    t = c.get("team", {})
    return {
        "abbr": t.get("abbreviation", "?"),
        "name": t.get("shortDisplayName") or t.get("name") or t.get("displayName", "?"),
        "full": t.get("displayName", ""),
        "color": t.get("color"),
        "record": next((r.get("summary") for r in c.get("records", []) if r.get("type") in ("total", None)), None),
    }


def parse_event(ev, season_type, week):
    comp = ev["competitions"][0]
    home = next(c for c in comp["competitors"] if c.get("homeAway") == "home")
    away = next(c for c in comp["competitors"] if c.get("homeAway") == "away")
    status = (comp.get("status") or ev.get("status") or {}).get("type", {})
    lines = lambda c: [int(float(l.get("value", 0) or 0)) for l in c.get("linescores", [])]
    return {
        "id": str(ev["id"]),
        "season_type": season_type,
        "week": (ev.get("week") or {}).get("number") or week,
        "date": ev.get("date"),
        "state": status.get("state"),          # pre / in / post
        "completed": bool(status.get("completed")),
        "home": team_info(home),
        "away": team_info(away),
        "score_home": int(float(home.get("score") or 0)),
        "score_away": int(float(away.get("score") or 0)),
        "lines_home": lines(home),
        "lines_away": lines(away),
    }


def fetch_week(year, season_type, week):
    q = urllib.parse.urlencode({"dates": year, "seasontype": season_type, "week": week})
    data = get(f"{BASE}/scoreboard?{q}")
    return [parse_event(ev, season_type, week) for ev in data.get("events", [])]


def fetch_summary(event_id):
    return get(f"{BASE}/summary?event={event_id}")


# ---------------------------------------------------------------- scoring plays

def clock_secs(clock):
    if not clock:
        return 0
    if isinstance(clock, dict):
        if clock.get("value") is not None:
            try:
                return float(clock["value"])
            except (TypeError, ValueError):
                pass
        disp = clock.get("displayValue", "0:00")
    else:
        disp = str(clock)
    m = re.match(r"(\d+):(\d+)", disp)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def scoring_events(summary):
    """Normalise scoring plays into a list of events in game order."""
    plays = summary.get("scoringPlays") or []
    if not plays:
        plays = [
            p
            for d in (summary.get("drives") or {}).get("previous", [])
            for p in d.get("plays", [])
            if p.get("scoringPlay")
        ]
    out, ph, pa = [], 0, 0
    for p in plays:
        h, a = p.get("homeScore"), p.get("awayScore")
        if h is None or a is None:
            continue
        h, a = int(h), int(a)
        dh, da = h - ph, a - pa
        if dh <= 0 and da <= 0:
            continue
        side = "home" if dh >= da else "away"
        pts = max(dh, da)
        typ = ((p.get("type") or {}).get("text") or "")
        sabbr = ((p.get("scoringType") or {}).get("abbreviation") or "").upper()
        text = p.get("text") or ""
        low = typ.lower()

        if "safety" in low or sabbr == "SF":
            kind = "safety"
        elif pts >= 6 or "touchdown" in low or sabbr == "TD":
            kind = "td"
        elif pts == 3 or "field goal" in low or sabbr == "FG":
            kind = "fg"
        else:
            kind = "conv"  # extra point / two-point try scored separately

        m = re.search(r"(\d+)\s*(?:Yd|yd|Yard|yard)", text)
        yards = int(m.group(1)) if m else int(p.get("statYardage") or 0)
        defst = kind == "safety" or (
            kind == "td" and bool(re.search(r"return|blocked|interception|fumble|defensive", typ + " " + text, re.I))
        )
        period = int((p.get("period") or {}).get("number") or 1)
        secs = clock_secs(p.get("clock"))
        out.append({
            "side": side, "pts": pts, "home": h, "away": a, "kind": kind,
            "yards": abs(yards), "defst": defst, "period": period,
            "clock": (p.get("clock") or {}).get("displayValue") if isinstance(p.get("clock"), dict) else None,
            "secs": secs,
        })
        ph, pa = h, a
    return out


def remaining_regulation(period, secs):
    if period > 4:
        return 0
    return (4 - period) * 900 + secs


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_extras(summary, game):
    """Box-score takeaways/sacks and the pre-game betting line, when present."""
    box = {}
    for t in (summary.get("boxscore") or {}).get("teams", []):
        abbr = (t.get("team") or {}).get("abbreviation")
        side = "home" if abbr == game["home"]["abbr"] else "away" if abbr == game["away"]["abbr"] else None
        if not side:
            continue
        stats = {s.get("name"): s for s in t.get("statistics", [])}
        def val(name):
            st = stats.get(name) or {}
            v = _num(st.get("value"))
            return v if v is not None else _num(st.get("displayValue"))
        to = val("turnovers")
        if to is None and (val("fumblesLost") is not None or val("interceptions") is not None):
            to = (val("fumblesLost") or 0) + (val("interceptions") or 0)
        sk = stats.get("sacksYardsLost") or {}
        box[side] = {"turnovers": to, "sacked": _num((sk.get("displayValue") or "").split("-")[0])}

    odds = None
    for pc in summary.get("pickcenter") or []:
        fav = None
        if (pc.get("homeTeamOdds") or {}).get("favorite"):
            fav = "home"
        elif (pc.get("awayTeamOdds") or {}).get("favorite"):
            fav = "away"
        spread = _num(pc.get("spread"))
        details = pc.get("details") or ""
        m = re.match(r"([A-Z]{2,4})\s+(-?\d+(?:\.\d+)?)", details)
        if m:
            fav = fav or ("home" if m.group(1) == game["home"]["abbr"] else "away" if m.group(1) == game["away"]["abbr"] else None)
            spread = spread if spread is not None else _num(m.group(2))
        if details.upper() == "EVEN":
            fav, spread = "home", 0.0
        if fav and spread is not None:
            odds = {"favorite": fav, "spread": abs(spread)}
            break
    return {"box": box or None, "odds": odds, "chaos": parse_chaos(summary)}


CHAOS_PATTERNS = [  # (key, regex, points)
    ("ejections", r"\bejected\b|\bdisqualified\b", 3.0),
    ("fights", r"\bfight(?:ing)?\b", 3.0),
    ("taunting", r"\btaunting\b", 1.5),
    ("unsportsmanlike", r"unsportsmanlike", 1.5),
    ("personal_fouls", r"unnecessary roughness|personal foul|roughing the passer|face ?mask", 0.5),
]


def parse_chaos(summary):
    counts = {k: 0 for k, _, _ in CHAOS_PATTERNS}
    for d in (summary.get("drives") or {}).get("previous", []):
        for p in d.get("plays", []):
            text = (p.get("text") or "").lower()
            if "penalty" not in text and "ejected" not in text and "disqualified" not in text:
                continue
            for k, rx, _ in CHAOS_PATTERNS:
                if re.search(rx, text):
                    counts[k] += 1
    return counts


def chaos_points(counts):
    if not counts:
        return 0.0
    return sum(counts.get(k, 0) * pts for k, _, pts in CHAOS_PATTERNS)


# ---------------------------------------------------------------- rating

def clamp(x, lo=0.0, hi=CAP):
    return max(lo, min(hi, x))


def elapsed(period, secs):
    if period <= 4:
        return (period - 1) * 900 + (900 - secs)
    return 3600 + (period - 5) * 600 + (600 - secs)


def nail_biter_share(events, ot):
    """Weighted share of the second half (Q4 and OT count double) within 8 points."""
    if not events:
        return 0.0
    end = max(3600, elapsed(events[-1]["period"], events[-1]["secs"])) if ot else 3600
    marks = [(elapsed(e["period"], e["secs"]), e["home"] - e["away"]) for e in events]
    t, diff, num, den = 0.0, 0, 0.0, 0.0
    for t_next, d_next in marks + [(end, None)]:
        lo, hi = max(t, 1800), min(t_next, end)
        if hi > lo:
            for a, b, w in ((lo, min(hi, 2700), 1.0), (max(lo, 2700), hi, 2.0)):
                if b > a:
                    den += (b - a) * w
                    if abs(diff) <= 8:
                        num += (b - a) * w
        t = max(t, t_next)
        if d_next is not None:
            diff = d_next
    return num / den if den else 0.0


def rate(game, events, extra=None, exp_margin=None):
    H, A = game["score_home"], game["score_away"]
    total, margin = H + A, abs(H - A)
    ot = len(game.get("lines_home", [])) > 4 or any(e["period"] > 4 for e in events)
    winner = "home" if H > A else "away" if A > H else None
    extra = extra or {}

    prev_sign, last_nonzero, changes, ties = 0, 0, 0, 0
    max_def = {"home": 0, "away": 0}
    decisive = None
    for i, e in enumerate(events):
        d = e["home"] - e["away"]
        s = (d > 0) - (d < 0)
        if s != 0 and last_nonzero != 0 and s != last_nonzero:
            changes += 1
        if s == 0 and prev_sign != 0:
            ties += 1
        if s != prev_sign:
            decisive = i
        if s != 0:
            last_nonzero = s
        prev_sign = s
        max_def["home"] = max(max_def["home"], -d)
        max_def["away"] = max(max_def["away"], d)

    close = clamp(1 - margin / 24, 0, 1) + (0.25 if ot else 0)

    if decisive is None:
        stw = 0.0
    else:
        e = events[decisive]
        if e["period"] > 4:
            stw = CAP
        else:
            r = remaining_regulation(e["period"], e["secs"])
            stw = 1.0 if r <= 120 else 0.75 if r <= 300 else 0.4 if r <= 600 else 0.2 if r <= 900 else 0.0

    comeback = max_def[winner] if winner else max(max_def.values())

    # Shootout vs defensive battle: count whichever is stronger.
    shootout = clamp((total - 24) / 30)
    low = clamp((37 - total) / 17, 0, 1)
    tight = clamp(1 - margin / 14, 0, 1)
    box = extra.get("box") or {}
    takeaways = sum((box.get(s) or {}).get("turnovers") or 0 for s in ("home", "away"))
    sacks = sum((box.get(s) or {}).get("sacked") or 0 for s in ("home", "away"))
    defensive = clamp(low * tight + 0.25 * low * clamp((takeaways + sacks / 3) / 6, 0, 1))
    style = max(shootout, defensive)

    # Upset: underdog by the betting line if we have it, else by form.
    upset, dog_by, line_source = 0.0, None, None
    odds = extra.get("odds")
    if odds and odds["spread"] > 0:
        dog = "away" if odds["favorite"] == "home" else "home"
        dog_by, line_source = odds["spread"], "line"
    elif exp_margin is not None and abs(exp_margin) >= 1:
        dog = "away" if exp_margin > 0 else "home"
        dog_by, line_source = abs(exp_margin), "form"
    if dog_by:
        if winner == dog:
            upset = clamp(dog_by / 9)
        elif winner is None:
            upset = clamp(dog_by / 18)

    factors = {
        "style": round(style, 3),
        "nail_biter": round(clamp(nail_biter_share(events, ot), 0, 1), 3),
        "lead_changes": round(clamp((changes + 0.5 * ties) / 4), 3),
        "upset": round(upset, 3),
        "close": round(clamp(close), 3),
        "score_to_win": round(clamp(stw), 3),
        "comeback": round(clamp(comeback / 14), 3),
        "big_plays": round(clamp(sum(1 for e in events if e["kind"] == "td" and e["yards"] >= 40) / 2), 3),
        "defence_st": round(clamp(sum(1 for e in events if e["defst"]) / 1.5), 3),
        "chaos": round(clamp(chaos_points(extra.get("chaos")) / 6), 3),
    }
    quality = round(BASE_POINTS + sum(WEIGHTS[k] * v for k, v in factors.items()), 1)
    return {
        "rating": quality,
        "factors": factors,
        "style_kind": "defensive" if defensive > shootout else "shootout",
        "chaos": extra.get("chaos"),
        "upset_source": line_source,
        "ot": ot,
        "lead_changes": changes,
        "ties": ties,
    }


# ---------------------------------------------------------------- notify

def notify(topic, games, site_url):
    if not topic or not games:
        return
    games = sorted(games, key=lambda g: -g["rating"])
    lines = [f"{g['away']['abbr']} @ {g['home']['abbr']}  {g['rating']:.0f}%  {g['tier_label']}" for g in games]
    top = games[0]
    title = f"{len(games)} game{'s' if len(games) != 1 else ''} rated"
    if top["tier"] == "now":
        title = "Must-watch game - avoid spoilers"
    elif top["tier"] == "full":
        title = f"{title} - one worth the full replay"
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data="\n".join(lines).encode("utf-8"),
        headers={
            "Title": title,
            "Click": site_url or "",
            "Tags": "football",
            "Priority": "high" if top["tier"] in ("now", "full") else "default",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=20).read()
        print(f"notified {len(games)} games")
    except Exception as exc:  # noqa: BLE001
        print(f"notify failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------- main

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


FIRST_SEASON = 2021
DATA_DIR = os.path.join(ROOT, "docs", "data")


def cache_path(year):
    return os.path.join(ROOT, "data", f"cache-{year}.json")


def summarise(g, r, stakes):
    """Game row for the page. Never includes a score unless finished and rated."""
    row = dict(g)
    row["stakes"] = stakes
    if r:
        row.update({k: r[k] for k in ("rating", "factors", "style_kind", "upset_source", "chaos", "ot", "lead_changes", "ties")})
        base, final, shift = verdict(r["rating"], stakes)
        row.update({"tier_base": base, "tier": final, "tier_label": TIER_LABEL[final], "shift": shift})
        row["plays"] = [
            {k: e[k] for k in ("side", "kind", "pts", "yards", "period", "clock", "home", "away", "defst")}
            for e in r["events"] if e["kind"] != "conv"
        ]
    else:
        row["score_home"] = row["score_away"] = None
        row["lines_home"] = row["lines_away"] = []
    return row


def build_season(year, fetch, cur=None):
    """Update one season. fetch=False rebuilds from cache only (no network)."""
    cache = load_json(cache_path(year), None)
    if cache is None and year == 2026:
        old = load_json(os.path.join(ROOT, "data", "cache.json"), {})  # pre-multi-season cache
        cache = {"rated": {k: {"events": v["events"]} for k, v in old.get("rated", {}).items()},
                 "notified": old.get("notified")}
        if cache["notified"] is None:
            cache.pop("notified")
    cache = cache or {}
    games = cache.get("games", {})
    events = {k: v["events"] for k, v in cache.get("rated", {}).items()}
    extras = cache.get("extras", {}) if cache.get("extras_version") == EXTRAS_VERSION else {}

    if fetch:
        jobs = [(year, 2, w) for w in range(1, 19)]
        if cur is None or cur["type"] >= 3:
            jobs += [(year, 3, w) for w in range(1, 6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            weeks = list(pool.map(lambda j: fetch_week(*j), jobs))
        for wk in weeks:
            for g in wk:
                games[g["id"]] = g

        todo = [g for g in games.values() if g["completed"] and (g["id"] not in events or g["id"] not in extras)]
        print(f"{year}: {len(games)} games, {len(todo)} to fetch")

        def work(g):
            try:
                summ = fetch_summary(g["id"])
                return g["id"], scoring_events(summ), parse_extras(summ, g)
            except Exception as exc:  # noqa: BLE001
                print(f"summary failed {g['id']}: {exc}", file=sys.stderr)
                return g["id"], None, None

        with ThreadPoolExecutor(max_workers=6) as pool:
            for gid, ev, ex in pool.map(work, todo):
                if ev is None:
                    continue
                extras[gid] = ex
                g = games[gid]
                if ev and (ev[-1]["home"], ev[-1]["away"]) != (g["score_home"], g["score_away"]):
                    print(f"warning {gid}: plays end {ev[-1]['home']}-{ev[-1]['away']}, "
                          f"final {g['score_home']}-{g['score_away']}", file=sys.stderr)
                events[gid] = ev

    # Stakes: recomputed for the live season; cached for finished ones.
    st_cache = cache.get("stakes")
    if fetch or cur is not None or not st_cache or cache.get("stakes_version") != STAKES_VERSION:
        st_cache = season_stakes(list(games.values()))
    stakes = {gid: v["stakes"] for gid, v in st_cache.items()}
    exp_margin = {gid: v.get("exp_margin") for gid, v in st_cache.items()}

    # Re-rate every finished game each run so weight changes apply everywhere.
    rated = {gid: {**rate(games[gid], ev, extras.get(gid), exp_margin.get(gid)), "events": ev}
             for gid, ev in events.items() if gid in games and games[gid]["completed"]}
    rows = [summarise(g, rated.get(g["id"]), stakes.get(g["id"]))
            for g in sorted(games.values(), key=lambda x: (x["season_type"], x["week"], x["date"] or ""))
            if not (g["season_type"] == 3 and g["week"] == 4)]  # drop the Pro Bowl

    complete = bool(rows) and cur is None and all(r.get("rating") is not None for r in rows if r["completed"])
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season": year,
        "current": cur is not None,
        "base_points": BASE_POINTS,
        "stakes_high": STAKES_HIGH,
        "factor_info": [{"key": k, "label": FACTOR_INFO[k][0], "points": WEIGHTS[k], "rule": FACTOR_INFO[k][1]}
                        for k in WEIGHTS],
        "stakes_dead": STAKES_DEAD,
        "weights": WEIGHTS,
        "tiers": [{"min": f, "key": k, "label": l} for f, k, l in TIERS],
        "games": rows,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, f"{year}.json"), "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    cache.update({"games": games, "rated": {k: {"events": v["events"]} for k, v in rated.items()},
                  "extras": extras, "extras_version": EXTRAS_VERSION, "stakes": st_cache, "stakes_version": STAKES_VERSION,
                  "complete": complete and all(gid in extras for gid in rated)})
    return cache, rows, games


def save_cache(year, cache):
    os.makedirs(os.path.dirname(cache_path(year)), exist_ok=True)
    with open(cache_path(year), "w") as f:
        json.dump(cache, f, separators=(",", ":"))


def main():
    current = get(f"{BASE}/scoreboard")
    season = current.get("season", {})
    year = int(season.get("year") or datetime.now(timezone.utc).year)
    stype = int(season.get("type") or 2)

    # Past seasons: fetch until complete, then rebuild from cache only.
    seasons = []
    for y in range(FIRST_SEASON, year):
        cached = load_json(cache_path(y), {})
        cache, rows, _ = build_season(y, fetch=not cached.get("complete") or cached.get("extras_version") != EXTRAS_VERSION)
        save_cache(y, cache)
        if rows:
            seasons.append(y)

    if stype == 1:  # preseason: rebuild this season from cache only
        cache, rows, games = build_season(year, fetch=False, cur={"type": 1})
    else:
        cache, rows, games = build_season(year, fetch=True, cur={"type": stype})
    if rows:
        seasons.append(year)

    # Notify once a round of games has finished: something new is final and
    # nothing is still in progress.
    first_run = "notified" not in cache
    notified = set(cache.get("notified", []))
    rated_rows = [g for g in rows if g.get("rating") is not None]
    fresh = [g for g in rated_rows if g["id"] not in notified]
    live = any(g["state"] == "in" for g in games.values())
    if first_run:
        notified |= {g["id"] for g in rated_rows}
    elif fresh and not live:
        notify(os.environ.get("NTFY_TOPIC"), fresh, os.environ.get("SITE_URL"))
        notified |= {g["id"] for g in fresh}
    cache["notified"] = sorted(notified)
    save_cache(year, cache)

    with open(os.path.join(DATA_DIR, "seasons.json"), "w") as f:
        json.dump({"seasons": sorted(seasons, reverse=True), "current": year}, f)
    old = os.path.join(ROOT, "data", "cache.json")
    if os.path.exists(old):
        os.remove(old)
    print(f"seasons: {seasons}")


if __name__ == "__main__":
    main()
