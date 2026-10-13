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

## Rating factors

Every game gets 10 base points, plus each factor's points at full strength
(factors can reach 125%, so games can exceed 100). Weights were calibrated
against nflgamerater.com's published 2026 Week 1 ratings.

| Factor | Points | Full strength at |
| --- | --- | --- |
| High scoring | 30 | 54 total points |
| Lead changes | 15 | 4 lead changes (a tie counts as half) |
| Big plays | 15 | Two touchdowns of 40+ yards |
| Defence / special teams | 15 | 1.5 defensive or return touchdowns / safeties |
| Close finish | 12 | Tied / 1-point margin; +25% for overtime |
| Late winning score | 10 | Winner went ahead for good in the last 2 minutes (OT = 125%) |
| Comeback | 10 | Winner overcame a 14-point deficit |

Tune `BASE_POINTS`, `WEIGHTS` and the divisors in `rate()` in `scripts/build.py`.

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
