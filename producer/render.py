#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: GPL-3.0-or-later
"""rows.json -> the exact character grid the eInk panel will draw.

All layout happens here, on the Mac. The payload that goes over MQTT is the
finished screen, so the device only draws lines it is handed. Every layout
change is a Mac-side edit with no reflash, and the terminal preview is what
the panel shows.

    ./render.py rows.json            # preview in the terminal
    ./render.py rows.json -o payload.txt

Geometry: a 400x300 SSD1683 panel through adafruit_epd's built-in 6x8 font
gives 66 columns x 21 rows at a 14px pitch, using 396x294 of 400x300.

Wire format: plain text, one line per screen row, at most 66 columns. A line
beginning with '~' is drawn white-on-black; that is the only marker.

A 4-gray variant was built and tried on real glass, shading each risk level a
different tone. It read badly: the 6x8 font is too fine for DARK and LIGHT to
stay crisp on e-ink, and the low-risk rows in particular turned mushy. Risk
stays encoded as the '>' gutter, the sort order, and the RISK column instead.
"""

import argparse
import json

COLS, ROWS = 66, 21
GLYPH_W, GLYPH_H = 6, 8
PITCH = 14

# gutter, number, author, age, files, diff, risk, status
W = (2, 7, 14, 5, 5, 12, 5, 16)  # 5-digit PR numbers need 7
assert sum(W) == COLS, sum(W)

MAX_ROWS = 10
RISK_LABEL = {2: "HIGH", 1: "med", 0: "low"}

INVERSE = "~"

STATUS_LABEL = {
    "approved": "approved",
    "changes_requested": "changes req.",
    "in_review": "in review",
    "unreviewed": "unreviewed",
    "draft": "draft",
}

def k(n):
    """66703 -> 66.7k, so a monster diff still fits the column."""
    if n < 10000:
        return str(n)
    if n < 1000000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n / 1000000:.1f}M"


def fit(text, width, align="<"):
    text = str(text)
    if len(text) > width:
        text = text[:width]
    return f"{text:{align}{width}}"


def pr_line(row):
    gutter = (">" if row["risk"] == 2 else " ") + ("*" if row["stale"] else " ")
    status = STATUS_LABEL.get(row["status"], row["status"])
    if row["ci_fail"]:
        status += " CI!"
    diff = f"+{k(row['additions'])}/-{k(row['deletions'])}"
    return (
        gutter
        + fit(f"#{row['number']}", W[1])
        + fit(row["author"], W[2])
        + fit(f"{row['age_days']}d", W[3], ">")
        + fit(f"{row['changed_files']}f", W[4], ">")
        + fit(diff, W[5] - 1, ">") + " "
        + fit(RISK_LABEL[row["risk"]], W[6])
        + fit(status, W[7])
    )


def attention_lines(rows, budget):
    """Every high row, plus any stale row regardless of risk, naming the
    specific concern that tripped it rather than just the label. Returns []
    rather than a lone truncation notice when there is no room to say
    anything useful."""
    if budget < 2:
        return []
    room = budget - 1
    flagged = [r for r in rows if r["risk"] == 2 or r["stale"]]
    if not flagged:
        return []

    # Only spend a line on the "and N more" counter when doing so does not
    # cost the last real note. With room for one line, the note wins.
    counter = len(flagged) > room and room >= 2
    shown = flagged[: room - 1] if counter else flagged[:room]

    out = [fit("  Needs attention:", COLS)]
    for row in shown:
        why = row["why"][0] if row["why"] else (
            "stale, no maintainer review" if row["stale"] else "flagged")
        out.append(fit(f"  #{row['number']} {why}", COLS))
    if counter:
        out.append(fit(f"  ...and {len(flagged) - len(shown)} more flagged", COLS))
    return out


def render(data):
    rows = data["rows"]
    repo = data["repo"]
    date = data["collected_at"]
    stale_days = data.get("stale_days", 180)
    window = data.get("window_days")

    lines = []
    title = f" PR QUEUE  {repo}"
    lines.append(INVERSE + fit(title, COLS - len(date) - 1) + date + " ")
    lines.append(
        " " * W[0] + fit("PR", W[1]) + fit("AUTHOR", W[2])
        + fit("AGE", W[3], ">") + fit("FILE", W[4], ">")
        + fit("DIFF", W[5] - 1, ">") + " "
        + fit("RISK", W[6]) + fit("STATUS", W[7]))
    lines.append("-" * COLS)

    fixed = len(lines) + 4  # + footer rule + 2 summary + 1 spare
    body_budget = ROWS - fixed
    shown = rows[: min(MAX_ROWS, body_budget)]
    lines.extend(pr_line(row) for row in shown)
    lines.extend(attention_lines(rows, body_budget - len(shown)))

    counts = [0, 0, 0]
    for row in rows:
        counts[row["risk"]] += 1
    by_status = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    n_stale = sum(1 for row in rows if row["stale"])

    while len(lines) < ROWS - 3:
        lines.append(" " * COLS)

    lines.append("-" * COLS)
    hidden = f" (+{len(rows) - len(shown)})" if len(rows) > len(shown) else ""
    scope = f" <{window}d" if window else ""
    lines.append(fit(
        f"  {len(rows)} open{scope}{hidden}   {counts[2]} high  {counts[1]} med  "
        f"{counts[0]} low   {n_stale} stale >{stale_days}d", COLS))
    lines.append(fit(
        "  " + "  ".join(
            f"{by_status.get(key, 0)} {label}"
            for key, label in (("approved", "appr"), ("changes_requested", "chg-req"),
                               ("in_review", "review"), ("unreviewed", "unrev"),
                               ("draft", "draft"))), COLS))

    return lines[:ROWS]


def serialize(lines):
    return "\n".join(line.rstrip() for line in lines)


def preview(lines):
    print(f"+{'-' * COLS}+   {COLS}x{len(lines)} chars "
          f"= {COLS * GLYPH_W}x{len(lines) * PITCH} px")
    for line in lines:
        inv = line.startswith(INVERSE)
        body = fit(line[1:] if inv else line, COLS)
        print(f"|\033[7m{body}\033[0m|" if inv else f"|{body}|")
    print(f"+{'-' * COLS}+")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("-o", "--out")
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    with open(args.infile) as fh:
        data = json.load(fh)
    lines = render(data)
    payload = serialize(lines)

    if not args.no_preview:
        preview(lines)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(payload)
        print(f"\n{len(payload)} bytes -> {args.out}")
    else:
        print(f"\npayload: {len(payload)} bytes")
    if len(payload) > 1024:
        print("  note: over 1KB, so the feed must have history OFF (100KB cap)")


if __name__ == "__main__":
    main()
