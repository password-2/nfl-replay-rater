import os, sys, json
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from sky import sky_games, text_lines

def team(abbr, name): return {"abbr": abbr, "name": name}
G = [
  {"id": "1", "season_type": 2, "week": 6, "date": "2026-10-16T00:15Z", "away": team("SEA","Seahawks"), "home": team("DEN","Broncos")},
  {"id": "2", "season_type": 2, "week": 6, "date": "2026-10-18T13:30Z", "away": team("HOU","Texans"), "home": team("JAX","Jaguars")},
  {"id": "3", "season_type": 2, "week": 6, "date": "2026-10-18T17:00Z", "away": team("CAR","Panthers"), "home": team("PHI","Eagles")},
  {"id": "4", "season_type": 2, "week": 6, "date": "2026-10-18T17:00Z", "away": team("NYJ","Jets"), "home": team("NE","Patriots")},
  {"id": "5", "season_type": 2, "week": 6, "date": "2026-10-18T20:25Z", "away": team("BUF","Bills"), "home": team("LV","Raiders")},
  {"id": "6", "season_type": 2, "week": 6, "date": "2026-10-18T20:25Z", "away": team("LAC","Chargers"), "home": team("KC","Chiefs")},
  {"id": "7", "season_type": 2, "week": 6, "date": "2026-10-18T17:00Z", "away": team("PIT","Steelers"), "home": team("TB","Buccaneers")},
]
# Synthetic page in the same shape as Sky's article (own wording).
page = """<h2>WEEK SIX</h2><h3>Thursday</h3>
<p>Seattle Seahawks @ Denver Broncos - <strong>KO 1.15am (Friday)</strong> - on Sky Sports NFL</p>
<p>Houston Texans vs Jacksonville Jaguars (Wembley) - <strong>KO 2.30pm</strong> - Sky Sports NFL</p>
<p>Carolina Panthers @ Philadelphia Eagles - <strong>KO 6pm</strong> - Sky Sports NFL</p>
<p>New York Jets @ New England Patriots - <strong>KO 6pm</strong> - Sky Sports+</p>
<p>Buffalo Bills @ Las Vegas Raiders - <strong>KO 9.25pm</strong> - Sky Sports NFL</p>
<p>Los Angeles Chargers @ Kansas City Chiefs - <strong>KO 9.25pm</strong> - Sky Sports Football</p>"""
out = sky_games(G, now=datetime(2026, 10, 14, tzinfo=timezone.utc), lines=text_lines(page))
print({k: (v["status"], v["uk_ko"]) for k, v in sorted(out.items())})
assert out["1"]["status"] == "dazn" and out["2"]["status"] == "dazn"
assert out["3"]["status"] == "blackout" and out["5"]["status"] == "blackout"
assert out["4"]["status"] == "maybe" and out["6"]["status"] == "maybe"
assert "7" not in out
print("ok")
