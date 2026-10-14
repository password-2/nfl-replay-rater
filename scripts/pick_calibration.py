#!/usr/bin/env python3
"""Pick ~30 memorable past games across the quality range for calibration.

Writes docs/data/calibrate.json with matchups and dates only: no scores,
ratings or anything else that would hint at the result.
"""
import json
import os
import random
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
SEASONS = range(2021, 2026)
STRATA = [(85, 999, 8), (70, 85, 7), (50, 70, 7), (0, 50, 8)]  # quality band, count
WANT = {"defensive": 3, "upset": 3, "chaos": 2}


def memorable(g):
    hour = datetime.fromisoformat(g["date"].replace("Z", "+00:00")).hour
    return g["season_type"] == 3 or hour in (0, 1)  # playoffs or US prime time


def tags(g):
    f = g["factors"]
    t = set()
    if g.get("style_kind") == "defensive" and f["style"] >= 0.4:
        t.add("defensive")
    if f["upset"] >= 0.5:
        t.add("upset")
    if f.get("chaos", 0) >= 0.5:
        t.add("chaos")
    return t


def main():
    rng = random.Random(2026)
    pool = []
    for y in SEASONS:
        for g in json.load(open(os.path.join(DATA, f"{y}.json")))["games"]:
            if g.get("rating") is not None and memorable(g):
                pool.append(g)
    rng.shuffle(pool)
    picked = []
    # Special types first, then fill each quality band.
    for kind, n in WANT.items():
        for g in [g for g in pool if kind in tags(g) and g not in picked][:n]:
            picked.append(g)
    for lo, hi, n in STRATA:
        have = sum(1 for g in picked if lo <= g["rating"] < hi)
        cands = [g for g in pool if lo <= g["rating"] < hi and g not in picked]
        cands.sort(key=lambda g: (g["season_type"] != 3, rng.random()))  # prefer playoffs a little
        for g in cands:
            if have >= n:
                break
            picked.append(g)
            have += 1

    rng.shuffle(picked)
    week = lambda g: ({1: "Wild Card", 2: "Divisional", 3: "Conference", 5: "Super Bowl"}.get(g["week"], "Playoffs")
                      if g["season_type"] == 3 else f"Week {g['week']}")
    season_of = lambda g: int(g["date"][:4]) - (1 if int(g["date"][5:7]) <= 3 else 0)
    out = [{
        "id": g["id"], "season": season_of(g), "week": week(g), "date": g["date"],
        "away": {k: g["away"][k] for k in ("abbr", "name")}, "home": {k: g["home"][k] for k in ("abbr", "name")},
    } for g in picked]
    with open(os.path.join(DATA, "calibrate.json"), "w") as f:
        json.dump({"games": out}, f, indent=0)
    bands = [sum(1 for g in picked if lo <= g["rating"] < hi) for lo, hi, _ in STRATA]
    print(len(out), "games; bands", bands, "playoffs", sum(g["season_type"] == 3 for g in picked),
          "seasons", sorted({season_of(g) for g in picked}),
          "tags", {k: sum(k in tags(g) for g in picked) for k in WANT})


if __name__ == "__main__":
    main()
