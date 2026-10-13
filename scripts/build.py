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

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Points each factor is worth at full strength (factor score 1.0), plus a
# base that every game gets. Factor scores are capped at 1.25, so a game can
# rate above 100%. Calibrated against nflgamerater.com's published 2026 Week 1
# ratings (five games within ~6 points, week average 55% vs 54%).
BASE_POINTS = 10
WEIGHTS = {
    "high_scoring": 30,   # Total points
    "lead_changes": 15,   # Lead changes and ties
    "big_plays": 15,      # Touchdowns of 40+ yards
    "defence_st": 15,     # Defensive / special teams scores and safeties
    "close": 12,          # Close game: final margin, overtime
    "score_to_win": 10,   # How late the winner took the lead for good
    "comeback": 10,       # Biggest deficit the winner overcame
}
CAP = 1.25

TIERS = [  # (minimum rating, key, short label)
    (100, "now", "WATCH THIS NOW"),
    (80, "full", "Full game (2h)"),
    (60, "condensed", "Condensed (40m)"),
    (0, "highlights", "Highlights (5m)"),
]


def tier_for(rating):
    for floor, key, label in TIERS:
        if rating >= floor:
            return key, label
    return TIERS[-1][1], TIERS[-1][2]


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


# ---------------------------------------------------------------- rating

def clamp(x, lo=0.0, hi=CAP):
    return max(lo, min(hi, x))


def rate(game, events):
    H, A = game["score_home"], game["score_away"]
    total, margin = H + A, abs(H - A)
    ot = len(game.get("lines_home", [])) > 4 or any(e["period"] > 4 for e in events)
    winner = "home" if H > A else "away" if A > H else None

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

    factors = {
        "close": round(clamp(close), 3),
        "score_to_win": round(clamp(stw), 3),
        "comeback": round(clamp(comeback / 14), 3),
        "lead_changes": round(clamp((changes + 0.5 * ties) / 4), 3),
        "high_scoring": round(clamp((total - 24) / 30), 3),
        "big_plays": round(clamp(sum(1 for e in events if e["kind"] == "td" and e["yards"] >= 40) / 2), 3),
        "defence_st": round(clamp(sum(1 for e in events if e["defst"]) / 1.5), 3),
    }
    rating = round(BASE_POINTS + sum(WEIGHTS[k] * v for k, v in factors.items()), 1)
    return {
        "rating": rating,
        "factors": factors,
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


def summarise(g, r):
    """Game row for the page. Never includes a score unless finished and rated."""
    row = dict(g)
    if r:
        row.update({k: r[k] for k in ("rating", "factors", "ot", "lead_changes", "ties")})
        row["tier"], row["tier_label"] = tier_for(r["rating"])
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

    if fetch:
        jobs = [(year, 2, w) for w in range(1, 19)]
        if cur is None or cur["type"] >= 3:
            jobs += [(year, 3, w) for w in range(1, 6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            weeks = list(pool.map(lambda j: fetch_week(*j), jobs))
        for wk in weeks:
            for g in wk:
                games[g["id"]] = g

        todo = [g for g in games.values() if g["completed"] and g["id"] not in events]
        print(f"{year}: {len(games)} games, {len(todo)} to fetch")

        def work(g):
            try:
                return g["id"], scoring_events(fetch_summary(g["id"]))
            except Exception as exc:  # noqa: BLE001
                print(f"summary failed {g['id']}: {exc}", file=sys.stderr)
                return g["id"], None

        with ThreadPoolExecutor(max_workers=6) as pool:
            for gid, ev in pool.map(work, todo):
                if ev is None:
                    continue
                g = games[gid]
                if ev and (ev[-1]["home"], ev[-1]["away"]) != (g["score_home"], g["score_away"]):
                    print(f"warning {gid}: plays end {ev[-1]['home']}-{ev[-1]['away']}, "
                          f"final {g['score_home']}-{g['score_away']}", file=sys.stderr)
                events[gid] = ev

    # Re-rate every finished game each run so weight changes apply everywhere.
    rated = {gid: {**rate(games[gid], ev), "events": ev}
             for gid, ev in events.items() if gid in games and games[gid]["completed"]}
    rows = [summarise(g, rated.get(g["id"]))
            for g in sorted(games.values(), key=lambda x: (x["season_type"], x["week"], x["date"] or ""))
            if not (g["season_type"] == 3 and g["week"] == 4)]  # drop the Pro Bowl

    complete = bool(rows) and all(r.get("rating") is not None for r in rows) and (cur is None)
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season": year,
        "current": cur is not None,
        "base_points": BASE_POINTS,
        "weights": WEIGHTS,
        "tiers": [{"min": f, "key": k, "label": l} for f, k, l in TIERS],
        "games": rows,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, f"{year}.json"), "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    cache.update({"games": games, "rated": {k: {"events": v["events"]} for k, v in rated.items()},
                  "complete": complete})
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
        cache, rows, _ = build_season(y, fetch=not cached.get("complete"))
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
