import os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from sky import blackouts

def team(abbr, name): return {"abbr": abbr, "name": name}
G = [
  {"id": "1", "season_type": 2, "week": 6, "date": "2026-10-16T00:15Z", "away": team("SEA","Seahawks"), "home": team("DEN","Broncos")},
  {"id": "3", "season_type": 2, "week": 6, "date": "2026-10-18T17:00Z", "away": team("CAR","Panthers"), "home": team("PHI","Eagles")},
  {"id": "4", "season_type": 2, "week": 6, "date": "2026-10-18T17:00Z", "away": team("NYJ","Jets"), "home": team("NE","Patriots")},
  {"id": "5", "season_type": 2, "week": 6, "date": "2026-10-18T20:25Z", "away": team("BUF","Bills"), "home": team("LV","Raiders")},
  {"id": "6", "season_type": 2, "week": 6, "date": "2026-10-18T20:25Z", "away": team("LAC","Chargers"), "home": team("KC","Chiefs")},
]
# Synthetic listings markup in the same shape as Sky's.
def ev(a, b, cap):
    return (f'<div class="event-group"><ul class="row-table event"><li class="col col1"><strong data-short-text="x">{a}</strong></li>'
            f'<li class="col col2">&#64</li><li class="col col3"><strong data-short-text="y">{b}</strong></li><li></li></ul>'
            f'<p class="event-detail -center caption"> {cap} </p></div>')
page = ('<h3>Fri 16th October</h3>' + ev("Seattle Seahawks", "Denver Broncos", "NFL, Sky Sports NFL (01:15)") +
        '<h3>Sun 18th October</h3>' + ev("New York Jets", "New England Patriots", "NFL - Week 6, Sky Sports+ (18:00)") +
        ev("Carolina Panthers", "Philadelphia Eagles", "NFL - Week 6, Sky Sports NFL (18:00), Sky Sports Main Event (18:00)") +
        ev("Buffalo Bills", "Las Vegas Raiders", "NFL - Week 6, Sky Sports NFL (21:25)") +
        ev("Los Angeles Chargers", "Kansas City Chiefs", "NFL - Week 6, Sky Sports Football (21:25)"))
out, problems, n = blackouts(G, now=datetime(2026, 10, 14, tzinfo=timezone.utc), page=page)
print(n, {k: (v["match"], v["uk_ko"]) for k, v in out.items()}, problems)
assert set(out) == {"3", "5"} and not problems and n == 5
print("ok")
