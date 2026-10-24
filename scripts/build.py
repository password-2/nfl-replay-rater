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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stakes import season_stakes  # noqa: E402
import sky  # noqa: E402

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
    "chaos": ("Chaos", "Fights and ejections (3 points each), taunting and unsportsmanlike conduct (1.5), offsetting "
              "penalties (1). Full at 4.5, e.g. three taunting flags. Only what appears in the play-by-play is caught."),
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

# STAKES (0-100, measured before kickoff) add up to +10 points to the verdict
# score, enough to tip a marginal game over a tier boundary. Low stakes never
# subtract: a good game is worth watching whatever was on the line.
STAKES_CURVE = [(0, 0), (40, 0), (90, 10), (100, 10)]
STAKES_VERSION = 3
EXTRAS_VERSION = 4


def stakes_adjust(stakes):
    if stakes is None:
        return 0.0
    pts = STAKES_CURVE
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if stakes <= x1:
            return round(y0 + (y1 - y0) * (max(stakes, x0) - x0) / (x1 - x0), 1)
    return float(pts[-1][1])


def tier_for(quality):
    for floor, key, label in TIERS:
        if quality >= floor:
            return key, label
    return TIERS[-1][1], TIERS[-1][2]


def verdict(quality, stakes):
    """Returns (base tier, final tier, shift, stakes adjustment, verdict score)."""
    base, _ = tier_for(quality)
    adj = stakes_adjust(stakes)
    score = round(quality + adj, 1)
    final, _ = tier_for(score)
    shift = TIER_KEYS.index(base) - TIER_KEYS.index(final)
    return base, final, shift, adj, score


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
    ("offsetting", r"offsetting", 1.0),
]
CHAOS_FULL = 4.5  # points for full marks: e.g. three taunting/unsportsmanlike flags


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
        "chaos": round(clamp(chaos_points(extra.get("chaos")) / CHAOS_FULL), 3),
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

ET = ZoneInfo("America/New_York")


def slot(g):
    """Short label for a game's TV window, from its US Eastern kickoff time."""
    try:
        ko = datetime.fromisoformat(g["date"].replace("Z", "+00:00")).astimezone(ET)
    except (KeyError, ValueError):
        return "?"
    day, hour = ko.strftime("%a"), ko.hour
    if day == "Sun":
        if hour < 12:
            return "intl"
        if hour < 15:
            return "1pm"
        if hour < 19:
            return "late"
        return "SNF"
    if hour >= 19 and day in ("Mon", "Thu"):
        return "MNF" if day == "Mon" else "TNF"
    return day


LAST_PUSH_ERROR = [None]


def notify(topic, games, site_url):
    if not topic or not games:
        return False
    games = [g for g in games if slot(g) != "1pm"]  # Sunday 18:00 UK games: watched live
    if not games:
        return True
    games = sorted(games, key=lambda g: -g["rating"])
    lines = [f"{g['away']['abbr']} @ {g['home']['abbr']} [{slot(g)}]  {g['rating']:.0f}%  {g['tier_label']}"
             for g in games]
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
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"notify failed: {exc}", file=sys.stderr)
        LAST_PUSH_ERROR[0] = str(exc)
        return False


# ---------------------------------------------------------------- main

FEEDBACK_TOPIC = "replay-rater-fb-c4ef88c2fe3e"  # public: the page posts "your verdict" votes here
VERDICTS = ("highlights", "condensed", "full", "now", "skip")


def write_json_if_changed(path, payload, ignore=("generated",)):
    """Avoid a commit every hour: only rewrite when the content changed."""
    old = load_json(path, None)
    payload = json.loads(json.dumps(payload))  # tuples -> lists, so it compares like the file
    strip = lambda d: {k: v for k, v in d.items() if k not in ignore} if isinstance(d, dict) else d
    if old is not None and strip(old) == strip(payload):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    return True


def collect_feedback(valid_ids):
    """Pull votes posted from the page via ntfy (ntfy keeps them ~12 hours)."""
    path = os.path.join(DATA_DIR, "feedback.json")
    fb = load_json(path, {"votes": {}, "seen": {}})
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{FEEDBACK_TOPIC}/json?poll=1&since=all",
                                     headers={"User-Agent": "nfl-game-rater/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            lines = resp.read().decode("utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001
        print(f"feedback poll failed: {exc}", file=sys.stderr)
        return
    now = time.time()
    for line in lines:
        try:
            m = json.loads(line)
            if m.get("event") != "message" or m["id"] in fb["seen"]:
                continue
            fb["seen"][m["id"]] = m.get("time", now)
            v = json.loads(m.get("message") or "{}")
            gid, verdict = str(v.get("g")), v.get("v")
            if gid in valid_ids and (verdict in VERDICTS or verdict is None):
                if verdict is None:
                    fb["votes"].pop(gid, None)
                else:
                    fb["votes"][gid] = {"v": verdict, "t": m.get("time", int(now))}
        except (ValueError, KeyError, TypeError):
            continue
    fb["seen"] = {k: t for k, t in fb["seen"].items() if now - t < 3 * 86400}
    write_json_if_changed(path, fb, ignore=("seen",))


def push(title, body, click="", tags="tv"):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
            headers={"Title": title, "Tags": tags, "Click": click}, method="POST"), timeout=20).read()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"push failed: {exc}", file=sys.stderr)
        return False


def update_sky(year, rows, state):
    """Record the two DAZN blackouts per week (from Sky's listings) and push once per week."""
    path = os.path.join(DATA_DIR, "sky.json")
    data = load_json(path, {"games": {}})
    try:
        found, problems, listed = sky.blackouts(rows)
    except Exception as exc:  # noqa: BLE001
        print(f"sky fetch failed: {exc}", file=sys.stderr)
        state["sky_last"] = {"error": str(exc)[:200]}
        return
    print(f"sky: {listed} listings, {len(found)} blackouts, problems {problems}")
    state["sky_last"] = {"listings": listed, "blackouts": sorted(i["match"] for i in found.values()),
                         "problems": problems}
    # Keep past weeks; replace this run's weeks wholesale so changes stick.
    weeks_now = {(i["season_type"], i["week"]) for i in found.values()}
    data["games"] = {k: v for k, v in data.get("games", {}).items()
                     if (v.get("season_type"), v.get("week")) not in weeks_now and v.get("window")}
    data["games"].update(found)
    write_json_if_changed(path, data)

    site = os.environ.get("SITE_URL") or ""
    sent = state.get("sky_sent", {})
    if os.environ.get("RESEND") == "true":
        sent = {k: v for k, v in sent.items() if not any(k == f"{year}-{st}-{wk}" for st, wk in weeks_now)}
        push("Replay Rater test", "Notifications are reaching this phone.", site)
    for st, wk in sorted(weeks_now):
        key = f"{year}-{st}-{wk}"
        games = sorted((i for i in found.values() if (i["season_type"], i["week"]) == (st, wk)), key=lambda i: i["uk_ko"])
        sig = ",".join(i["match"] for i in games)
        if sent.get(key) == sig:
            continue
        body = "\n".join(f'{i["match"]}  {i["uk_ko"]}' for i in games)
        if any(p.startswith(f"Week {wk}:") for p in problems):
            body += "\nCheck Sky: more than one Sky Sports NFL game in a window."
        title = f"Week {wk}: not live on DAZN" + (" (changed)" if key in sent else "")
        if push(title, body, site + (f"#{year}/w{wk}" if site else "")):
            sent[key] = sig
    state["sky_sent"] = sent
    for k in ("sky_notified",):
        state.pop(k, None)

    # Validate the rule against DAZN's help page; flag if its wording changes.
    fp, ok = sky.dazn_rule_fingerprint()
    if fp:
        if state.get("dazn_rule") and state["dazn_rule"] != fp:
            push("DAZN blackout rules page changed",
                 "DAZN edited its UK blackout help page. Worth a look in case the 6pm / 9pm rule changed.", sky.DAZN_URL)
        if ok is False and state.get("dazn_rule_ok") is not False:
            push("DAZN blackout rule check failed",
                 "DAZN's help page no longer mentions the 6pm / 9pm windows. Blackout picks may be wrong.", sky.DAZN_URL)
        state["dazn_rule"], state["dazn_rule_ok"] = fp, ok


def preseason_reminder(year, state):
    """One push about two weeks before the regular season starts."""
    starts = []
    for y in (year, year + 1):
        try:
            starts += [g["date"] for g in fetch_week(y, 2, 1) if g.get("date")]
        except Exception:  # noqa: BLE001
            pass
    now = datetime.now(timezone.utc)
    future = sorted(d for d in starts if datetime.fromisoformat(d.replace("Z", "+00:00")) > now)
    if not future:
        return
    first = datetime.fromisoformat(future[0].replace("Z", "+00:00"))
    days = (first - now).days
    key = first.strftime("%Y")
    if days <= 14 and state.get("reminded") != key:
        topic = os.environ.get("NTFY_TOPIC")
        if topic:
            body = (f"The {key} season starts {first.strftime('%a %d %b')}. Replay Rater is running. "
                    "If no ratings appear after the first game, check the Actions tab on GitHub.")
            try:
                urllib.request.urlopen(urllib.request.Request(
                    f"https://ntfy.sh/{topic}", data=body.encode(),
                    headers={"Title": "NFL season in two weeks", "Tags": "football",
                             "Click": os.environ.get("SITE_URL") or ""}, method="POST"), timeout=20).read()
                state["reminded"] = key
                print("sent preseason reminder")
            except Exception as exc:  # noqa: BLE001
                print(f"reminder failed: {exc}", file=sys.stderr)


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
        base, final, shift, adj, score = verdict(r["rating"], stakes)
        row.update({"tier_base": base, "tier": final, "tier_label": TIER_LABEL[final], "shift": shift,
                    "stakes_adj": adj, "score": score})
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
        reuse = st_cache if cache.get("stakes_version") == STAKES_VERSION else None
        st_cache = season_stakes(list(games.values()), cached=reuse)
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
        "stakes_curve": STAKES_CURVE,
        "factor_info": [{"key": k, "label": FACTOR_INFO[k][0], "points": WEIGHTS[k], "rule": FACTOR_INFO[k][1]}
                        for k in WEIGHTS],
        "weights": WEIGHTS,
        "tiers": [{"min": f, "key": k, "label": l} for f, k, l in TIERS],
        "games": rows,
    }
    write_json_if_changed(os.path.join(DATA_DIR, f"{year}.json"), payload)

    cache.update({"games": games, "rated": {k: {"events": v["events"]} for k, v in rated.items()},
                  "extras": extras, "extras_version": EXTRAS_VERSION, "stakes": st_cache, "stakes_version": STAKES_VERSION,
                  "complete": complete and all(gid in extras for gid in rated)})
    return cache, rows, games


def save_cache(year, cache):
    write_json_if_changed(cache_path(year), cache, ignore=())


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

    state_path = os.path.join(ROOT, "data", "state.json")
    state = load_json(state_path, {})
    if stype in (1, 4):  # preseason / offseason: nothing to fetch for this season yet
        preseason_reminder(year, state)
        cache, rows, games = build_season(year, fetch=False, cur={"type": stype})
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
    # Hold the push while an afternoon/evening UK kickoff (12:00-22:59) is
    # still to come in the next 6 hours, so a Sunday's London and 6pm games go
    # out with the 9pm window. Overnight (prime-time) games still get their own.
    now_utc = datetime.now(timezone.utc)

    def _held_by(g):
        if g["state"] != "pre" or not g.get("date"):
            return False
        ko = datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
        return now_utc < ko <= now_utc + timedelta(hours=6) and 12 <= ko.astimezone(sky.UK).hour < 23

    later_today = any(_held_by(g) for g in games.values())
    if first_run:
        notified |= {g["id"] for g in rated_rows}
    elif fresh and not live and not later_today:
        if notify(os.environ.get("NTFY_TOPIC"), fresh, os.environ.get("SITE_URL")):
            notified |= {g["id"] for g in fresh}
        else:
            state["last_push_error"] = LAST_PUSH_ERROR[0] or "no topic or nothing to send"
    cache["notified"] = sorted(notified)
    save_cache(year, cache)

    write_json_if_changed(os.path.join(DATA_DIR, "seasons.json"),
                          {"seasons": sorted(seasons, reverse=True), "current": year})

    if stype in (2, 3) and rows:
        update_sky(year, rows, state)

    valid = set()
    for y in seasons:
        valid |= {g["id"] for g in load_json(os.path.join(DATA_DIR, f"{y}.json"), {}).get("games", [])}
    collect_feedback(valid)

    # Heartbeat: one small commit a month so GitHub never pauses the schedule
    # in the off-season (it does after 60 days without commits).
    state["heartbeat"] = datetime.now(timezone.utc).strftime("%Y-%m")
    write_json_if_changed(state_path, state, ignore=())
    old = os.path.join(ROOT, "data", "cache.json")
    if os.path.exists(old):
        os.remove(old)
    print(f"seasons: {seasons}")


if __name__ == "__main__":
    main()
