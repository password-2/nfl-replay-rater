"""Run the build, and keep re-running it while games are on.

One pass of build.py, then: if a game is live or kicks off within
WATCH_AHEAD, stay running and re-check ESPN every POLL seconds, committing
whenever a game goes final (build.py sends the push once the round is over).
GitHub stops a job after 6 hours, so before then the watcher starts a fresh
copy of the workflow and exits. Outside game windows it is a single pass,
as before.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
POLL = 5 * 60                       # seconds between checks while watching
WATCH_AHEAD = timedelta(hours=4)    # stay on if a game kicks off within this
LATE_START = timedelta(hours=2)     # a "pre" game this far past kickoff is ignored
HANDOVER = 5 * 3600 + 30 * 60       # hand over to a fresh run after 5h30m


def sh(*cmd, check=True):
    return subprocess.run(cmd, cwd=ROOT, check=check, capture_output=True, text=True)


def games():
    try:
        year = json.load(open(os.path.join(DATA, "seasons.json")))["current"]
        return json.load(open(os.path.join(DATA, f"{year}.json")))["games"]
    except Exception:  # noqa: BLE001
        return []


def finals(gs):
    return {g["id"] for g in gs if g.get("completed")}


def notified():
    try:
        year = json.load(open(os.path.join(DATA, "seasons.json")))["current"]
        return set(json.load(open(os.path.join(ROOT, "data", f"cache-{year}.json"))).get("notified", []))
    except Exception:  # noqa: BLE001
        return set()


def sync():
    """Start every pass from the latest commit, so a push already recorded by
    another run is never sent again."""
    sh("git", "pull", "-q", "--rebase", "--autostash", "origin", "main", check=False)


def should_watch(gs, now):
    for g in gs:
        if g.get("state") == "in":
            return True
        if g.get("state") == "pre" and g.get("date"):
            try:
                ko = datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if now - LATE_START <= ko <= now + WATCH_AHEAD:
                return True
    return False


def build():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build.py")], cwd=ROOT)
    if r.returncode:
        print(f"build.py exited {r.returncode}", file=sys.stderr)


def commit():
    sh("git", "add", "-A", "docs", "data")
    if sh("git", "diff", "--cached", "--quiet", check=False).returncode == 0:
        return
    sh("git", "commit", "-q", "-m", "Update ratings")
    for _ in range(3):
        sh("git", "pull", "-q", "--rebase", "--autostash", check=False)
        if sh("git", "push", "-q", check=False).returncode == 0:
            print("committed")
            return
        time.sleep(10)
    print("push failed", file=sys.stderr)


def hand_over():
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/update.yml/dispatches",
        data=json.dumps({"ref": "main"}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=20).read()
        print("handed over to a fresh run")
    except Exception as exc:  # noqa: BLE001
        print(f"hand-over failed: {exc}", file=sys.stderr)


def main():
    start = time.time()
    sync()
    build()
    commit()
    seen = (finals(games()), notified())
    while should_watch(games(), datetime.now(timezone.utc)):
        if time.time() - start > HANDOVER:
            hand_over()
            return
        print(f"watching: next check in {POLL // 60} min")
        time.sleep(POLL)
        os.environ["RESEND"] = "false"  # a test push only on the first pass
        sync()
        build()
        now = (finals(games()), notified())
        if now != seen:  # a game went final or a push went out: record it now
            commit()
            seen = now
    commit()


if __name__ == "__main__":
    main()
