#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pull an open PR queue from GitHub into raw.json.

Mechanical only: this runs `gh` and reshapes the result. No judgment, no
scoring. Everything here is a fact you could check yourself.

    ./collect.py adafruit/circuitpython --limit 40 -o raw.json
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

FIELDS = [
    "number", "title", "author", "createdAt", "updatedAt",
    "additions", "deletions", "changedFiles", "isDraft",
    "files", "body", "reviews", "reviewDecision", "statusCheckRollup",
]

# gh silently caps the `files` array at 100 entries. Past that the list is a
# lie, and the file it hides may be exactly the shared-code touch that matters.
GH_FILES_CAP = 100

# Reviews from these accounts are signal, but they are not a maintainer verdict.
BOT_REVIEWERS = {
    "copilot-pull-request-reviewer", "github-actions", "claude",
    "codecov-commenter", "sonarcloud", "coderabbitai",
}


def run_gh(repo, state, limit):
    cmd = [
        "gh", "pr", "list", "--repo", repo, "--state", state,
        "--limit", str(limit), "--json", ",".join(FIELDS),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"gh failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def days_since(iso, now):
    if not iso:
        return None
    stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (now - stamp).days


def review_state(pr, now):
    """Latest verdict per human reviewer, plus when a human last engaged."""
    human_verdicts = {}
    last_human_activity = None
    bot_reviews = 0

    for review in pr.get("reviews") or []:
        login = ((review.get("author") or {}).get("login") or "").lower()
        verdict = review.get("state")
        submitted = review.get("submittedAt")
        if login in BOT_REVIEWERS or login.endswith("[bot]"):
            bot_reviews += 1
            continue
        if verdict in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED"):
            human_verdicts[login] = verdict
        if submitted and (last_human_activity is None or submitted > last_human_activity):
            last_human_activity = submitted

    verdicts = set(human_verdicts.values())
    if pr.get("isDraft"):
        status = "draft"
    elif "CHANGES_REQUESTED" in verdicts:
        status = "changes_requested"
    elif "APPROVED" in verdicts:
        status = "approved"
    elif verdicts:
        status = "in_review"
    else:
        status = "unreviewed"

    return {
        "status": status,
        "human_reviewers": len(human_verdicts),
        "bot_reviews": bot_reviews,
        "days_since_review": days_since(last_human_activity, now),
    }


def ci_state(pr):
    """Failing check names. Whether a failure is this PR's fault or ambient
    noise is a judgment call, so we only report what failed."""
    failing = []
    for check in pr.get("statusCheckRollup") or []:
        bad = check.get("conclusion") in ("FAILURE", "TIMED_OUT", "CANCELLED") \
            or check.get("state") in ("FAILURE", "ERROR")
        if bad:
            failing.append(check.get("name") or check.get("context") or "?")
    return failing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="owner/repo")
    ap.add_argument("--state", default="open")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--since-days", type=int, default=None,
                    help="only PRs opened within this many days")
    ap.add_argument("-o", "--out", default="raw.json")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    local_date = datetime.now().strftime("%Y-%m-%d")  # wall date, not UTC
    prs = []

    for pr in run_gh(args.repo, args.state, args.limit):
        age = days_since(pr.get("createdAt"), now)
        if args.since_days is not None and (age is None or age > args.since_days):
            continue
        files = [f["path"] for f in (pr.get("files") or [])]
        changed = pr.get("changedFiles") or len(files)
        failing = ci_state(pr)
        prs.append({
            "number": pr["number"],
            "title": pr.get("title") or "",
            "author": ((pr.get("author") or {}).get("login") or "?"),
            "body": pr.get("body") or "",
            "age_days": age,
            "additions": pr.get("additions") or 0,
            "deletions": pr.get("deletions") or 0,
            "changed_files": changed,
            "files": files,
            "files_truncated": changed > GH_FILES_CAP,
            "is_draft": bool(pr.get("isDraft")),
            "ci_failing": failing,
            "review": review_state(pr, now),
        })

    out = {
        "repo": args.repo,
        "collected_at": local_date,
        "state": args.state,
        "window_days": args.since_days,
        "prs": prs,
    }
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)

    truncated = sum(1 for p in prs if p["files_truncated"])
    window = f" opened in the last {args.since_days}d" if args.since_days else ""
    print(f"{len(prs)} PRs from {args.repo}{window} -> {args.out}")
    if truncated:
        print(f"  warning: {truncated} PR(s) have >{GH_FILES_CAP} files; "
              f"their file lists are capped by gh and incomplete")


if __name__ == "__main__":
    main()
