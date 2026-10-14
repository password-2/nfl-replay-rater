import os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from sky import blackouts, text_lines

def team(abbr, name): return {"abbr": abbr, "name": name}
G = [
  {"id": "1", "season_type": 2, "week": 6, "date": "2026-10-16T00:15Z", "away": team("SEA","Seahawks"), "home": team("DEN","Broncos")},
  {"id": "3", "season_type": 2, "week": 6, "date": "2026-10-18T17:00Z", "away": team("CAR","Panthers"), "home": team("PHI","Eagles")},
  {"id": "4", "season_type": 2, "week": 6, "date": "2026-10-18T17:00Z", "away": team("NYJ","Jets"), "home": team("NE","Patriots")},
  {"id": "5", "season_type": 2, "week": 6, "date": "2026-10-18T20:25Z", "away": team("BUF","Bills"), "home": team("LV","Raiders")},
  {"id": "6", "season_type": 2, "week": 6, "date": "2026-10-18T20:25Z", "away": team("LAC","Chargers"), "home": team("KC","Chiefs")},
]
# Synthetic listings page in the same shape as Sky's (own wording).
page = """<h3>Fri 16th October</h3><div><b>Seattle Seahawks</b> @ <b>Denver Broncos</b></div>
<div>NFL - Week 6, Sky Sports NFL (01:00)</div>
<h3>Sun 18th October</h3>
<div><b>New York Jets</b> @ <b>New England Patriots</b></div><div>NFL - Week 6, Sky Sports+ (18:00)</div>
<div><b>Carolina Panthers</b> @ <b>Philadelphia Eagles</b></div><div>NFL - Week 6, Sky Sports NFL (18:00), Sky Sports Main Event (18:00)</div>
<div><b>Buffalo Bills</b> @ <b>Las Vegas Raiders</b></div><div>NFL - Week 6, Sky Sports NFL (21:25)</div>
<div><b>Los Angeles Chargers</b> @ <b>Kansas City Chiefs</b></div><div>NFL - Week 6, Sky Sports Football (21:25)</div>"""
out, problems, n = blackouts(G, now=datetime(2026, 10, 14, tzinfo=timezone.utc), lines=text_lines(page))
print(n, {k: (v["match"], v["uk_ko"]) for k, v in out.items()}, problems)
assert set(out) == {"3", "5"} and not problems and n == 5
print("ok")
