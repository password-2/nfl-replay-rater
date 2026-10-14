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
  {"id": "8", "season_type": 2, "week": 6, "date": "2026-10-18T17:00Z", "away": team("CHI","Bears"), "home": team("ATL","Falcons")},
]
# Synthetic page in the same shape as Sky's article (own wording).
page = """<h2>WEEK SIX</h2><h3>Thursday</h3>
<p>Seattle Seahawks @ Denver Broncos - <strong>KO 1.15am (Friday)</strong> - on Sky Sports NFL</p>
<p>Houston Texans vs Jacksonville Jaguars (Wembley) - <strong>KO 2.30pm</strong> - Sky Sports NFL</p>
<p>Carolina Panthers @ Philadelphia Eagles - <strong>KO 6pm</strong> - Sky Sports NFL</p>
<p>New York Jets @ New England Patriots - <strong>KO 6pm</strong> - Sky Sports+</p>
<p>Buffalo Bills @ Las Vegas Raiders - <strong>KO 9.25pm</strong> - Sky Sports NFL</p>
<p>Los Angeles Chargers @ Kansas City Chiefs - <strong>KO 9.25pm</strong> - Sky Sports Football</p>"""
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
out = sky_games(G, now=NOW, lines=text_lines(page), tracker="")
print({k: (v["status"], v["uk_ko"]) for k, v in sorted(out.items())})
assert out["1"]["status"] == "dazn" and out["2"]["status"] == "dazn"
assert out["3"]["status"] == "blackout" and out["5"]["status"] == "blackout"
assert out["4"]["status"] == "maybe" and out["6"]["status"] == "maybe"
assert "7" not in out

# Channel 5 info only (no DAZN column filled): Bills @ Raiders is on 5, so the 9pm blackout moves.
ch5_only = """| Game | Time | Channel 5 | Sky Sports | DAZN |
|---|---|---|---|---|
| Bears @ Falcons | Sun 18:00 | \u2705 5a | \u274c | |
| Bills @ Raiders | Sun 21:25 | \u2705 5 | \u2705 NFL | |
"""
out2 = sky_games(G, now=NOW, lines=text_lines(page), tracker=ch5_only)
print({k: (v["status"], v["channels"]) for k, v in sorted(out2.items())})
assert out2["5"]["status"] == "dazn" and out2["6"]["status"] == "blackout"
assert out2["3"]["status"] == "blackout" and out2["4"]["status"] == "maybe"
assert out2["8"]["channels"] == ["5Action"] and out2["8"]["status"] == "dazn"

# Full tracker with DAZN column: it wins.
full = ch5_only.replace("| \u274c | |", "| \u274c | \u2705 |").replace("\u2705 NFL | |", "\u2705 NFL | \u2705 |") + \
    "| Jets @ Patriots | Sun 18:00 | \u274c | \u2705 + | \u2705 |\n| Panthers @ Eagles | Sun 18:00 | \u274c | \u2705 NFL | \u274c |\n"
out3 = sky_games(G, now=NOW, lines=text_lines(page), tracker=full)
assert out3["3"]["status"] == "blackout" and out3["3"]["source"] == "tracker"
assert out3["4"]["status"] == "dazn" and out3["5"]["status"] == "dazn"
print("ok")
