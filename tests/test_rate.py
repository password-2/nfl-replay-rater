import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from build import scoring_events, rate, tier_for

def play(h, a, q, clock, typ, text=""):
    return {"homeScore": h, "awayScore": a, "period": {"number": q},
            "clock": {"displayValue": clock}, "type": {"text": typ}, "text": text}

# Thriller: lead swaps, late comeback, 55-yd TD, pick-six, walk-off in OT
thriller = {"scoringPlays": [
    play(7, 0, 1, "10:00", "Passing Touchdown", "55 Yd pass"),
    play(7, 7, 1, "3:00", "Rushing Touchdown", "2 Yd run"),
    play(14, 7, 2, "8:00", "Rushing Touchdown", "1 Yd run"),
    play(14, 14, 2, "0:30", "Interception Return Touchdown", "40 Yd interception return"),
    play(14, 21, 3, "6:00", "Passing Touchdown", "12 Yd pass"),
    play(14, 24, 4, "9:00", "Field Goal Good", "41 Yd field goal"),
    play(21, 24, 4, "4:00", "Passing Touchdown", "8 Yd pass"),
    play(24, 24, 4, "0:04", "Field Goal Good", "52 Yd field goal"),
    play(30, 24, 5, "6:10", "Rushing Touchdown", "3 Yd run"),
]}
g = {"score_home": 30, "score_away": 24, "lines_home": [7, 7, 0, 10, 6]}
r = rate(g, scoring_events(thriller))
print("thriller", r["rating"], tier_for(r["rating"]), r["factors"])
assert r["rating"] >= 85

# Blowout
blow = {"scoringPlays": [play(7 * i, 0, min(4, 1 + i // 2), "5:00", "Rushing Touchdown", "3 Yd run") for i in range(1, 6)]}
g2 = {"score_home": 35, "score_away": 0, "lines_home": [7, 14, 7, 7]}
r2 = rate(g2, scoring_events(blow))
print("blowout", r2["rating"], tier_for(r2["rating"]))
assert r2["rating"] < 40

# Drive-derived plays with separate PAT plays
drives = {"drives": {"previous": [{"plays": [
    {"scoringPlay": True, "homeScore": 6, "awayScore": 0, "period": {"number": 1}, "clock": {"displayValue": "9:00"}, "type": {"text": "Pass Reception"}, "text": "X pass to Y for 45 yards, TOUCHDOWN"},
    {"scoringPlay": True, "homeScore": 7, "awayScore": 0, "period": {"number": 1}, "clock": {"displayValue": "9:00"}, "type": {"text": "Extra Point Good"}, "text": ""},
]}]}}
ev = scoring_events(drives)
print([ (e["kind"], e["yards"]) for e in ev])
assert [e["kind"] for e in ev] == ["td", "conv"] and ev[0]["yards"] == 45
print("ok")
