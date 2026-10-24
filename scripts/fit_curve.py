"""Fit QUALITY_CURVE (raw factor points -> quality) so that, across every
finished game since 2021, the verdict tiers split as Adam wants:

    prime-time games (TNF/SNF/MNF): ~30% highlights, ~50% condensed,
    a full game every 2-3 weeks (~14%), 2-3 watch-now a season (~5%)

Run after changing WEIGHTS; paste the printed curve into build.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from build import WEIGHTS, curve_points, slot  # noqa: E402

TARGET = {60: 0.30, 80: 0.81, 100: 0.95}  # share of games below each boundary
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data")


def games():
    for f in sorted(os.listdir(DATA)):
        if f[:4].isdigit():
            for g in json.load(open(os.path.join(DATA, f)))["games"]:
                if g.get("completed") and g.get("factors") and g["season_type"] == 2 \
                        and slot(g) in ("TNF", "SNF", "MNF"):
                    yield sum(WEIGHTS[k] * v for k, v in g["factors"].items()), g.get("stakes_adj") or 0


def share_below(curve, rows, line):
    return sum(curve_points(s, curve) + a < line for s, a in rows) / len(rows)


def fit():
    rows = list(games())
    xs = {60: 30.0, 80: 60.0, 100: 82.0}
    for _ in range(30):
        for line in (60, 80, 100):
            lo = {60: 1, 80: xs[60] + 1, 100: xs[80] + 1}[line]
            hi = {60: xs[80] - 1, 80: xs[100] - 1, 100: 140}[line]
            for _ in range(40):
                mid = (lo + hi) / 2
                xs[line] = mid
                curve = [(0, 10), (xs[60], 60), (xs[80], 80), (xs[100], 100)]
                if share_below(curve, rows, line) > TARGET[line]:
                    hi = mid
                else:
                    lo = mid
    curve = [(0, 10)] + [(round(xs[l], 1), l) for l in (60, 80, 100)]
    n = len(rows)
    tiers = [share_below(curve, rows, 60), share_below(curve, rows, 80), share_below(curve, rows, 100)]
    print(f"{n} games; split {tiers[0]:.0%} / {tiers[1]-tiers[0]:.0%} / {tiers[2]-tiers[1]:.0%} / {1-tiers[2]:.0%}")
    print("QUALITY_CURVE =", curve)


if __name__ == "__main__":
    fit()
