#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: GPL-3.0-or-later
"""POST a rendered payload into an Adafruit IO feed via its webhook URL.

The webhook URL carries its own token, so no API key travels with the
request. Adafruit IO is the broker: this pushes in over HTTPS, the panel
pulls out over MQTT.

    export AIO_WEBHOOK_URL=https://io.adafruit.com/api/v2/webhooks/feed/XXXX
    ./push.py payload.txt

Feed setup matters. A feed with history ON caps a single value at 1024
bytes, and a full 66x21 panel is around 1.1-1.4KB. Turn history OFF in the
feed settings (Feed > Feed Info > History) and the cap becomes 100KB. There
is no reason to keep history for a display feed anyway: only the latest
value is ever drawn, and MQTT `/get` returns it on demand.
"""

import argparse
import hashlib
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

WEBHOOK_HOST = "io.adafruit.com"
HISTORY_ON_LIMIT = 1024
HISTORY_OFF_LIMIT = 100 * 1024
STATE_FILE = pathlib.Path(".push-state.json")


def normalize_url(url):
    """Validate the webhook URL without ever echoing it.

    The URL is a credential: it is the only thing standing between the
    internet and your feed. So every message here describes the SHAPE that
    was wrong, never the value, which also keeps it out of CI logs.
    """
    if not url:
        return None, ("no webhook URL: set AIO_WEBHOOK_URL or pass --url")
    url = url.strip()
    if url.startswith("http://"):
        return None, ("webhook URL is http://, refusing to send a credential "
                      "in the clear. Use https://")
    if not url.startswith("https://"):
        # The overwhelmingly common paste error is dropping the scheme.
        if url.startswith(WEBHOOK_HOST + "/"):
            return "https://" + url, None
        return None, (
            "webhook URL has no https:// scheme "
            f"({len(url)} chars, starts {url[:4]!r}...). "
            f"Expected https://{WEBHOOK_HOST}/api/v2/webhooks/feed/<token>")
    return url, None


def post(url, value, timeout=15):
    body = json.dumps({"value": value}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:400]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payload", help="file from render.py, or - for stdin")
    ap.add_argument("--url", default=os.environ.get("AIO_WEBHOOK_URL"))
    ap.add_argument("--force", action="store_true",
                    help="push even if the payload is byte-identical to last time")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    value = sys.stdin.read() if args.payload == "-" \
        else pathlib.Path(args.payload).read_text()
    size = len(value.encode())
    digest = hashlib.sha256(value.encode()).hexdigest()[:16]

    if size > HISTORY_OFF_LIMIT:
        sys.exit(f"payload is {size} bytes, over the 100KB feed maximum")
    if size > HISTORY_ON_LIMIT:
        print(f"note: {size} bytes needs history OFF on the feed "
              f"(the 1KB cap applies only with history on)")

    # An eInk panel should not repaint for an identical screen: a full 4.2"
    # refresh takes ~15s of flashing. The device checks this too, but not
    # sending is cheaper than sending and discarding.
    if not args.force and STATE_FILE.exists():
        last = json.loads(STATE_FILE.read_text()).get("digest")
        if last == digest:
            print(f"unchanged ({digest}), not pushing. --force to override.")
            return

    if args.dry_run:
        print(f"[dry run] would POST {size} bytes ({digest})")
        return

    url, problem = normalize_url(args.url)
    if problem:
        sys.exit("push.py: " + problem)

    status, body = post(url, value)
    if status != 200:
        sys.exit(f"webhook returned {status}: {body}")
    STATE_FILE.write_text(json.dumps({"digest": digest, "bytes": size}))
    print(f"pushed {size} bytes ({digest})")


if __name__ == "__main__":
    main()
