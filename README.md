# Replay Rater

Spoiler-free entertainment ratings for every NFL game, so you know whether to
watch the full replay, the condensed game or just the highlights.

- `scripts/build.py` fetches the season from ESPN's public feed every hour,
  rates each finished game and writes `docs/data.json`.
- `docs/index.html` is the page (GitHub Pages serves the `docs` folder).
- `.github/workflows/update.yml` runs the build hourly and commits the results.
- When a round of games finishes (something new is final and nothing is still
  live), it sends one phone notification through ntfy, linking to the page.

## Verdicts

| Rating | Verdict |
| --- | --- |
| 100+ | Watch this now, avoid all spoilers |
| 80-100 | Full game (2h) |
| 60-80 | Condensed (40m) |
| under 60 | Highlights (5m), browse freely |

## Two scores

**Quality** (how good the game was to watch): 10 base points plus each factor's
points at full strength. Factors cap at 125%, so quality can pass 100.

| Factor | Points | Full strength at |
| --- | --- | --- |
| Shootout or defensive battle | 26 | 54 total points, or a close game with 20 or fewer (whichever scores higher) |
| Nail-biter | 14 | Whole second half within 8 points (Q4/OT count double) |
| Lead changes | 10 | 4 lead changes (a tie counts as half) |
| Upset | 10 | A 9-point underdog wins (betting line; falls back to form) |
| Close finish | 8 | Tied / 1-point margin; +25% for overtime |
| Late winning score | 8 | Winner went ahead for good in the last 2 minutes (OT = 125%) |
| Comeback | 8 | Winner overcame a 14-point deficit |
| Big plays | 8 | Two touchdowns of 40+ yards |
| Defence / special teams | 8 | 1.5 defensive or return touchdowns / safeties |
| Chaos | 6 | Fights and ejections (3 each), taunting / unsportsmanlike (1.5), personal fouls (0.5); full at 6 |

**Stakes** (0-100, measured before kickoff): standings are rebuilt from earlier
results, the rest of the season is simulated 1,500 times, and the game's score is
the swing in both teams' chances of making the playoffs, winning the division
and taking the 1 seed. Playoff games score 70 (Wild Card) to 100 (Super Bowl).

**Verdict score** = quality + a stakes bonus of 0 to +10 points: nothing at
stakes 40 or below, rising to +10 at 90+. Enough to tip a marginal game over a
tier boundary; low stakes never mark a good game down. The page shows the
verdict without stakes wherever stakes changed it.

Tune `WEIGHTS` and `STAKES_CURVE` in `scripts/build.py`, and `LEVERAGE_FULL`
in `scripts/stakes.py`.

## Setup

1. Create a **public** repository and push these files (Pages is free on public repos).
2. Settings > Pages > Build and deployment: Source "Deploy from a branch",
   branch `main`, folder `/docs`.
3. Settings > Secrets and variables > Actions > New repository secret:
   `NTFY_TOPIC` = a long random name, e.g. `replay-rater-7f3k9q2m`.
4. Install the ntfy app on your phone and subscribe to that same topic name.
5. Actions tab > Update ratings > Run workflow (the first run backfills the
   season without sending a notification).

Note: GitHub pauses scheduled workflows after 60 days with no commits. In the
off-season that will happen; re-enable it from the Actions tab in September.

## Local run

```
python3 scripts/build.py
python3 tests/test_rate.py
python3 -m http.server -d docs 8000
```
