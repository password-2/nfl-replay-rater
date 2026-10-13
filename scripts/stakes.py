"""Playoff stakes: how much a game mattered, measured before kickoff.

For each regular-season week, rebuild the standings from earlier results,
simulate the rest of the season many times, then split the simulations by
who won each game that week. The swing in each team's chances of making the
playoffs, winning the division and taking the 1 seed (the only bye) is the
game's leverage.

Tiebreakers use win percentage, then division record, then conference
record, then a coin flip. Head-to-head and the deeper NFL steps are skipped.
"""
import math
import random

DIVISIONS = {
    "AFC East": ["BUF", "MIA", "NE", "NYJ"],
    "AFC North": ["BAL", "CIN", "CLE", "PIT"],
    "AFC South": ["HOU", "IND", "JAX", "TEN"],
    "AFC West": ["DEN", "KC", "LV", "LAC"],
    "NFC East": ["DAL", "NYG", "PHI", "WSH"],
    "NFC North": ["CHI", "DET", "GB", "MIN"],
    "NFC South": ["ATL", "CAR", "NO", "TB"],
    "NFC West": ["ARI", "LAR", "SF", "SEA"],
}
ALIASES = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "SD": "LAC"}
DIV_OF = {t: d for d, ts in DIVISIONS.items() for t in ts}
CONF_OF = {t: d.split()[0] for t, d in DIV_OF.items()}

PLAYOFF_STAKES = {1: 70, 2: 80, 3: 90, 5: 100}  # Wild Card, Divisional, Conference, Super Bowl
SIMS = 1500
LEVERAGE_FULL = 1.5  # leverage that scores 100 stakes
HOME_EDGE = 1.5     # points
SPREAD_SCALE = 6.5  # logistic scale for margin -> win probability
SHRINK = 3          # games of regression to zero for early-season strength


def norm(abbr):
    return ALIASES.get(abbr, abbr)


def win_prob(margin):
    return 1 / (1 + math.exp(-margin / SPREAD_SCALE))


class Table:
    """Wins (ties count half), division wins and conference wins per team."""

    def __init__(self):
        self.w = {t: 0.0 for t in DIV_OF}
        self.g = {t: 0 for t in DIV_OF}
        self.dw = {t: 0.0 for t in DIV_OF}
        self.cw = {t: 0.0 for t in DIV_OF}

    def copy(self):
        c = Table.__new__(Table)
        c.w, c.g, c.dw, c.cw = dict(self.w), dict(self.g), dict(self.dw), dict(self.cw)
        return c

    def add(self, home, away, home_share):
        """home_share: 1 home win, 0 away win, 0.5 tie."""
        for t, s in ((home, home_share), (away, 1 - home_share)):
            self.w[t] += s
            self.g[t] += 1
            if DIV_OF[home] == DIV_OF[away]:
                self.dw[t] += s
            if CONF_OF[home] == CONF_OF[away]:
                self.cw[t] += s


def seed(table, rng):
    """Return (playoff teams, division winners, 1 seeds) as sets."""
    key = lambda t: (table.w[t], table.dw[t], table.cw[t], rng.random())
    playoffs, div_winners, ones = set(), set(), set()
    for conf in ("AFC", "NFC"):
        winners = [max(ts, key=key) for d, ts in DIVISIONS.items() if d.startswith(conf)]
        div_winners.update(winners)
        ranked = sorted(winners, key=lambda t: (table.w[t], table.cw[t], rng.random()), reverse=True)
        ones.add(ranked[0])
        rest = sorted((t for t in DIV_OF if CONF_OF[t] == conf and t not in winners),
                      key=lambda t: (table.w[t], table.cw[t], rng.random()), reverse=True)
        playoffs.update(winners + rest[:3])
    return playoffs, div_winners, ones


def strengths(results):
    """Points-per-game margin, shrunk toward zero early in the season."""
    pd = {t: 0.0 for t in DIV_OF}
    n = {t: 0 for t in DIV_OF}
    for h, a, hs, as_ in results:
        pd[h] += hs - as_
        pd[a] += as_ - hs
        n[h] += 1
        n[a] += 1
    return {t: pd[t] / (n[t] + SHRINK) for t in DIV_OF}


def season_stakes(games, seed_value=7):
    """games: list of game dicts for one season.

    Returns {game_id: {"stakes": 0-100, "exp_margin": float (home view) or None}}.
    """
    rng = random.Random(seed_value)
    reg = [g for g in games if g["season_type"] == 2]
    out = {}

    for g in games:
        if g["season_type"] == 3 and g["week"] in PLAYOFF_STAKES:
            out[g["id"]] = {"stakes": PLAYOFF_STAKES[g["week"]], "exp_margin": None}

    for week in range(1, 19):
        this_week = [g for g in reg if g["week"] == week]
        before = [g for g in reg if g["week"] < week]
        if not this_week:
            continue
        if any(not g["completed"] and g.get("state") != "post" for g in before):
            break  # standings before this week aren't final yet
        try:
            pairs = [(norm(g["home"]["abbr"]), norm(g["away"]["abbr"])) for g in reg]
            if any(h not in DIV_OF or a not in DIV_OF for h, a in pairs):
                continue
        except KeyError:
            continue

        base = Table()
        results = []
        for g in before:
            if not g["completed"]:
                continue  # cancelled game
            h, a = norm(g["home"]["abbr"]), norm(g["away"]["abbr"])
            hs, as_ = g["score_home"], g["score_away"]
            base.add(h, a, 1.0 if hs > as_ else 0.0 if as_ > hs else 0.5)
            results.append((h, a, hs, as_))
        st = strengths(results)

        remaining = []
        for g in reg:
            if g["week"] >= week:
                h, a = norm(g["home"]["abbr"]), norm(g["away"]["abbr"])
                remaining.append((g["id"], h, a, win_prob(st[h] - st[a] + HOME_EDGE)))
        week_ids = {g["id"] for g in this_week}

        # counts[gid][outcome][team] = [n, playoffs, division, one_seed]
        counts = {gid: {0: {}, 1: {}} for gid in week_ids}
        teams_of = {gid: (h, a) for gid, h, a, _ in remaining if gid in week_ids}
        for gid, (h, a) in teams_of.items():
            for o in (0, 1):
                for t in (h, a):
                    counts[gid][o][t] = [0, 0, 0, 0]

        for _ in range(SIMS):
            tbl = base.copy()
            outcome = {}
            for gid, h, a, p in remaining:
                home_won = 1 if rng.random() < p else 0
                tbl.add(h, a, float(home_won))
                if gid in week_ids:
                    outcome[gid] = home_won
            playoffs, divs, ones = seed(tbl, rng)
            for gid, (h, a) in teams_of.items():
                c = counts[gid][outcome[gid]]
                for t in (h, a):
                    row = c[t]
                    row[0] += 1
                    row[1] += t in playoffs
                    row[2] += t in divs
                    row[3] += t in ones

        for gid, (h, a) in teams_of.items():
            lev = 0.0
            for t in (h, a):
                hw, aw = counts[gid][1][t], counts[gid][0][t]
                if hw[0] == 0 or aw[0] == 0:
                    continue
                d = [abs(hw[i] / hw[0] - aw[i] / aw[0]) for i in (1, 2, 3)]
                lev += d[0] + 0.5 * d[1] + 0.5 * d[2]
            out[gid] = {
                "stakes": round(min(100.0, lev / LEVERAGE_FULL * 100), 1),
                "exp_margin": round(st[h] - st[a] + HOME_EDGE, 1),
            }
    return out
