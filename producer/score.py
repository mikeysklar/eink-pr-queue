#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Score raw.json on the five concerns from pr-queue-view.md -> rows.json.

Three of the five concerns are mechanical and scored here. Two of them
(APIs/interfaces, single problem) need a reading of the diff and the
surrounding convention, so they come out `null` and a model is expected to
fill them in. Overall risk is the WORST of the scored concerns, never an
average, exactly as the recipe specifies.

    ./score.py raw.json -o rows.json
    ./score.py raw.json --core-path py/ --core-path shared-bindings/

To override a judgment, edit rows.json: set concerns.apis or concerns.single
to 0/1/2 and re-run `./score.py --rescore rows.json` to recompute overall.
"""

import argparse
import json
import re
import sys

LOW, MED, HIGH = 0, 1, 2

# Directories where a bug has the widest blast radius, per repo. Anything not
# listed falls back to `--core-path`, and with neither the concern reports
# `null` rather than pretending to know the repo's layout.
CORE_PATHS = {
    "adafruit/circuitpython": ["py/", "shared-bindings/", "shared-module/",
                               "supervisor/", "extmod/", "lib/tinyusb/"],
    "hathach/tinyusb": ["src/common/", "src/device/", "src/host/", "src/tusb.c",
                        "src/tusb.h", "src/osal/"],
}

DOC_SUFFIXES = (".md", ".rst", ".txt", ".po", ".pot")

# A testing claim, not a testing guarantee. For firmware repos a named board
# beats "compiles clean", so we grade those two differently.
HARDWARE_CLAIM = re.compile(
    r"\b(tested (on|with|it|this)|verified on|ran (it|this|the)|flashed|"
    r"reproduced on|confirmed on|works on my|on a real)\b", re.I)
WEAK_CLAIM = re.compile(
    r"\b(compiles?|builds? (clean|fine|ok)|ci (is )?green|no (build )?errors)\b", re.I)


def top_dirs(files):
    return {f.split("/")[0] for f in files if "/" in f}


def score_size(pr):
    """Caps at MED on purpose. The concern is whether the diff is proportional
    to the stated problem, and proportionality needs the problem read. A big
    diff for a big well-scoped problem is not a flag, so raw churn alone never
    earns HIGH here."""
    churn = pr["additions"] + pr["deletions"]
    if churn >= 300:
        return MED, f"{churn} lines changed"
    return LOW, None


def score_files(pr, core_paths):
    n = pr["changed_files"]
    if core_paths is None:
        if pr["files_truncated"]:
            return MED, f"{n} files, list capped by gh at 100 - blast radius unknown"
        return (MED, f"{n} files touched") if n > 10 else (LOW, None)

    hits = [f for f in pr["files"] if any(f.startswith(p) for p in core_paths)]
    if hits:
        # Basenames only: this string lands on a 66-column panel and the
        # directory prefix is the part the reader can already infer.
        # Dedupe: shared-bindings/.../Serial.c and shared-module/.../Serial.c
        # are different files but the same basename, and printing it twice
        # reads as a bug.
        names = sorted({h.rsplit("/", 1)[-1] for h in hits})
        more = f" +{len(names) - 3}" if len(names) > 3 else ""
        return HIGH, f"{len(hits)} core: {', '.join(names[:3])}{more}"
    if pr["files_truncated"]:
        return MED, f"{n} files, list capped by gh at 100 - core touch may be hidden"
    if n > 10:
        return MED, f"{n} files touched, none in core paths"
    return LOW, None


def score_tested(pr):
    """Also caps at MED. A missing testing claim is a flag, not a verdict, and
    on a two-line change it is barely even that. Where a missing claim is
    genuinely dangerous is when the PR touches core paths, and that is already
    carried by the files concern, so scoring it HIGH here would double-count."""
    body = pr["body"]
    churn = pr["additions"] + pr["deletions"]
    code_files = [f for f in pr["files"] if not f.lower().endswith(DOC_SUFFIXES)]
    if not code_files:
        return LOW, None
    if HARDWARE_CLAIM.search(body):
        return LOW, None
    if WEAK_CLAIM.search(body):
        return (MED, "build/compile claim only, no runtime test named") \
            if churn >= 50 else (LOW, None)
    if churn < 50 and pr["changed_files"] <= 2:
        return LOW, None
    if not body.strip():
        return MED, "empty PR body, no testing claim at all"
    return MED, "no testing claim in body"


def score_single(pr):
    """Weak proxy: a diff fanning across many top-level trees is more likely a
    grab-bag. Genuinely deciding this needs the diff read, so MED is the
    ceiling here and a model should overwrite it."""
    dirs = top_dirs(pr["files"])
    if pr["files_truncated"]:
        return None, None
    if len(dirs) > 5:
        return MED, f"spans {len(dirs)} top-level trees: {', '.join(sorted(dirs)[:5])}"
    return None, None


def status_code(pr, stale_days):
    review = pr["review"]
    status = review["status"]
    stale = (review["days_since_review"] is None and pr["age_days"] >= stale_days) or \
            (review["days_since_review"] is not None and review["days_since_review"] >= stale_days)
    return status, stale


def rescore(row):
    scored = [v for v in row["concerns"].values() if v is not None]
    row["risk"] = max(scored) if scored else LOW
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("-o", "--out", default="rows.json")
    ap.add_argument("--core-path", action="append", dest="core_paths")
    ap.add_argument("--stale-days", type=int, default=180)
    ap.add_argument("--rescore", action="store_true",
                    help="input is rows.json; just recompute overall risk and re-sort")
    args = ap.parse_args()

    with open(args.infile) as fh:
        data = json.load(fh)

    if args.rescore:
        data["rows"] = [rescore(r) for r in data["rows"]]
    else:
        core_paths = args.core_paths or CORE_PATHS.get(data["repo"])
        if core_paths is None:
            print(f"note: no core paths known for {data['repo']}; "
                  f"blast-radius scoring falls back to file count. "
                  f"Pass --core-path to fix.", file=sys.stderr)

        rows = []
        for pr in data["prs"]:
            size, size_why = score_size(pr)
            files, files_why = score_files(pr, core_paths)
            tested, tested_why = score_tested(pr)
            single, single_why = score_single(pr)
            status, stale = status_code(pr, args.stale_days)

            row = {
                "number": pr["number"],
                "author": pr["author"],
                "title": pr["title"],
                "age_days": pr["age_days"],
                "changed_files": pr["changed_files"],
                "additions": pr["additions"],
                "deletions": pr["deletions"],
                "status": status,
                "stale": stale,
                "ci_fail": bool(pr["ci_failing"]),
                "ci_failing": pr["ci_failing"][:3],
                "concerns": {
                    "size": size,
                    "files": files,
                    "apis": None,     # needs a diff read - a model fills this in
                    "tested": tested,
                    "single": single,
                },
                # Worst concern first, so a renderer with room for one line
                # names the concern that actually set the overall risk rather
                # than whichever one happens to be listed first.
                "why": [w for _, w in sorted(
                    ((size, size_why), (files, files_why),
                     (tested, tested_why), (single, single_why)),
                    key=lambda pair: -(pair[0] or 0)) if w],
            }
            rows.append(rescore(row))
        data["rows"] = rows
        data.pop("prs", None)

    # Worst-first, then oldest-first within a tier, to surface the backlog and
    # not only what is scary.
    data["rows"].sort(key=lambda r: (-r["risk"], -(r["age_days"] or 0)))

    data["stale_days"] = args.stale_days
    with open(args.out, "w") as fh:
        json.dump(data, fh, indent=2)

    counts = [0, 0, 0]
    for r in data["rows"]:
        counts[r["risk"]] += 1
    unjudged = sum(1 for r in data["rows"] if r["concerns"]["apis"] is None)
    print(f"{len(data['rows'])} rows -> {args.out}  "
          f"({counts[HIGH]} high, {counts[MED]} med, {counts[LOW]} low)")
    if unjudged:
        print(f"  {unjudged} row(s) have concerns.apis unscored - mechanical pass only")


if __name__ == "__main__":
    main()
