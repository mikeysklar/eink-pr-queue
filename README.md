# eink-pr-queue

![The rig in its 3D printed case: a 4.2 inch eInk panel behind a pink bezel showing open CircuitPython pull requests, with a QR of this repo in the corner; the Feather RP2040 ThinkInk and AirLift FeatherWing sit on a doubler in the open tray below](pics/eink-pr-queue.jpeg)

A Feather RP2040 ThinkInk and AirLift paint a GitHub pull request risk
dashboard onto 4.2 inch eInk via Adafruit IO.

## Bill of materials

| Part | Adafruit | Price |
|---|---|---|
| 4.2" 400x300 mono / 4-gray eInk, SSD1683, bare display | [#6381](https://www.adafruit.com/product/6381) | $24.95 |
| Feather RP2040 ThinkInk, 24-pin E-Paper connector | [#5727](https://www.adafruit.com/product/5727) | $17.50 |
| AirLift FeatherWing, ESP32 WiFi co-processor | [#4264](https://www.adafruit.com/product/4264) | $12.95 |
| FeatherWing Doubler | [#2890](https://www.adafruit.com/product/2890) | $7.50 |
| | | **$62.90** |


Prices 2026-08-29. Ribbon plugs into the ZIF socket. The wing needs headers
soldered; stacking headers work instead of the doubler.

## How it connects

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  GITHUB ACTIONS          cron "0 * * * *"  (hourly, on the hour) │
  │  .github/workflows/      also: workflow_dispatch (manual)        │
  │    update-panel.yml             repository_dispatch(pr-changed)  │
  │                                                                  │
  │  collect.py ──▶ score.py ──▶ render.py ──▶ push.py               │
  │  gh pr list     5 concerns   66x21 text    HTTPS POST            │
  │  --since-days   worst-of-5   + QR url      always: a fresh       │
  │      90         sorts rows   narrow rows   VM keeps no state     │
  │                                                                  │
  │  secret: AIO_WEBHOOK_URL   (a capability URL, not the account    │
  │                             key, so the runner holds no AIO_KEY) │
  └───────────────────────────────────────────────────┬──────────────┘
                                                      │ {"value": "..."}
                                                      ▼
                        https://io.adafruit.com/api/v2/webhooks/feed/<token>
                                                      │
                                           ┌──────────┴──────────┐
                                           │  feed: pr-queue     │
                                           │  history: OFF       │
                                           │  rate limit: 2/min  │
                                           └──────────┬──────────┘
                                                      │ MQTT/TLS :8883
                                                      │ <user>/f/pr-queue
                                                      │ + /get replay on boot
                                                      ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  FEATHER DOUBLER                                                 │
  │   Feather RP2040 ThinkInk  ◀── header SPI ──▶  AirLift (ESP32)   │
  │   CircuitPython 10.3            CS=D13         nina-fw 3.3.0     │
  │                                 READY=D11                        │
  │                                 RESET=D12                        │
  │                                                                  │
  │   hash the payload: identical screen, no repaint                 │
  │   hold, never drop, inside the 180s refresh floor                │
  └──────────────┼───────────────────────────────────────────────────┘
                 │ onboard 24-pin ZIF, SPI0, a SEPARATE bus
                 ▼
        ┌─────────────────────────────┐
        │  4.2" 400x300 SSD1683       │
        │  #6381, FPC-190 ribbon      │
        │  66 cols x 21 rows          │
        │  8 to 11s full refresh      │
        └─────────────────────────────┘
```

The webhook is the way in, MQTT the way out. The panel cannot receive an
inbound push, so it subscribes.

## How the three talk

```
┌─ GitHub runner ──────────────────────────────────────┐
│  gh → api.github.com          HTTPS GET              │
│                               auth: github.token     │
│                               (auto, expires w/ job) │
└──────────────────────┬───────────────────────────────┘
                       │  HTTPS POST {"value": "..."}
                       │  auth: the token IS the URL
                       ▼
┌─ Adafruit IO ────────────────────────────────────────┐
│  feed pr-queue, history off, keeps only latest value │
└──────────────────────┬───────────────────────────────┘
                       │  MQTT over TLS, port 8883
                       │  auth: <username> + AIO_KEY
                       ▼
┌─ ThinkInk + AirLift ─────────────────────────────────┐
│  SUBSCRIBE <username>/f/pr-queue   ← waits, no poll  │
│  PUBLISH  <username>/f/pr-queue/get  (on boot only)  │
└──────────────────────────────────────────────────────┘
```

- **They never meet.** IO is a mailbox. Either side can be offline.
- **The panel never polls.** It holds an MQTT socket open and gets written to.
- **Credentials are split.** The runner gets a one-feed capability URL. The
  account key never leaves the board.
- **`/get`** is the one upstream message: replay the value so a fresh boot is
  not blank.

## The one wiring rule

Panel goes in the ZIF socket. Via EyeSPI its RST and BUSY collide with the
AirLift. D13 is also `board.LED`, so it flickers.

| | Bus | Pins |
|---|---|---|
| Panel | SPI0, onboard ZIF | `EPD_SCK`/`MOSI` GPIO22/23, `CS`/`DC`/`RESET`/`BUSY` GPIO16-19 |
| AirLift | Feather header SPI | SCK/MOSI/MISO GPIO14/15/8, CS=D13, READY=D11, RESET=D12 |

## What is in here

```
.github/workflows/
  update-panel.yml        hourly cron, renders and POSTs to the feed

producer/                 runs on GitHub's runners, not your machine
  collect.py              gh pr list        -> raw.json
  score.py                five concerns, sorts worst-first -> rows.json
  render.py               layout + the QR url -> the 66x21 screen
  push.py                 POST to the Adafruit IO feed webhook
  update.sh               all four, for running it by hand

device/
  code.py                 CircuitPython: subscribe, compare, draw
  settings.toml.example   copy to settings.toml on CIRCUITPY and fill in

pics/
  eink-pr-queue.jpeg      the panel running, used at the top of this file
```

Stdlib Python plus `gh`, so it runs locally exactly as in CI.

## Producer

```sh
cd producer
DRY_RUN=1 ./update.sh adafruit/circuitpython --since-days 90   # render only
./update.sh adafruit/circuitpython --limit 200                 # render and push
```

| Step | Does | Judgment? |
|---|---|---|
| `collect.py` | `gh pr list` into `raw.json` | none, facts only |
| `score.py` | five concerns into `rows.json` | three of five, mechanically |
| `render.py` | the 66x21 panel text | layout only |
| `push.py` | POST to the feed webhook | none |

### Scoring

Worst of five, never the average.

| Concern | Ceiling | Why |
|---|---|---|
| Size | med | proportionality needs the problem read |
| Files | **HIGH** | core-path blast radius, the only mechanical HIGH |
| APIs | `null` | needs the diff read against merged work nearby |
| Tested | med | a missing claim is a flag, not a verdict |
| Single problem | med | weak proxy: diff fanning across top-level trees |

Edit `rows.json` and recompute:

```sh
./score.py .work/rows.json --rescore -o .work/rows.json
```

Core paths live in `CORE_PATHS`. Unlisted repos need `--core-path`.

### Feed setup

Turn feed history **OFF**, or every push fails on size.

| History | Max bytes per value |
|---|---|
| on | 1,024 |
| off | 524,288 |

```sh
export AIO_WEBHOOK_URL="https://io.adafruit.com/api/v2/webhooks/feed/XXXXXXXX"
```

The token is the URL, so no API key travels.

## Wire format

The finished screen, not data. Layout is host-side, so redesigning never
means reflashing.

```
66 columns x 21 rows, newline separated, trailing spaces stripped
a line beginning with '~' is drawn white-on-black (the title bar)
a line beginning with '@' is not a row: it carries a URL to draw as a QR
```

```
>*#10283 rianadon       492d  13f    +517/-1 HIGH changes req. CI!
||
|+-- '*' stale, no maintainer review past --stale-days (180)
+--- '>' overall risk High
```

Last 8 rows narrow to 47 columns for the QR.

## Notes from building it

| Decision | Why |
|---|---|
| Mono, not 4-gray | tried on glass, the 6x8 font is too fine for mid tones. Also 15KB not 30KB, 8s not 10.5s |
| `adafruit_epd`, not displayio | displayio collapses the X window to ~8px of speckle on panels 256px or wider |
| QR built on device | host sends a 43 byte URL, not a bitmap. 0.66s to generate, 0.81s to draw |
| QR at ECC Q | 25% recovery survives a photo of a photo. Verified at 5.6px per module |
| NeoPixel as status | a wall panel cannot report for itself without burning an 8s refresh |
| Hold, never drop | a payload arriving inside the 180s floor waits instead of being discarded |

RAM was never the constraint: 67KB free of 264KB. Verify a QR by decoding,
not by comparing matrices.

## Why this polls instead of using a webhook

Repository webhooks need admin. A PR cannot add one.

> "The GitHub triggers in Pipedream enable you to get notified immediately via
> a webhook if you have admin rights on the repo you're watching [...]
> Otherwise you can still poll for updates at a regular interval for any other
> repo where you might not have admin rights."
> [Pipedream](https://github.com/PipedreamHQ/pipedream/blob/master/components/github/README.md)

| Mechanism | No admin? | Push or poll |
|---|---|---|
| Repository webhooks | no | push |
| GitHub App install | no, needs org owner | push |
| PubSubHubbub | no, defunct | n/a |
| Atom feeds | no PR or issue feed exists | poll |
| Public events API | **yes** | poll, [30s to 6h](https://docs.github.com/en/rest/activity/events) |
| Actions from a third-party repo | no | n/a |
| **Watch + email** | **yes** | **genuine push** |

Hosted pollers need no admin either. Rejected on merit:

- Make's free floor is 15 minutes. The cron runs hourly.
- No bridge can score or render, so the workflow still runs. It could only
  trigger, which means a GitHub token in a third party.
- A `pull_request` event is 35KB and gives `changed_files` as an integer with
  no paths, so core-path scoring needs a re-fetch regardless.
- Adafruit's own [PyPortal GitHub Stars Trophy](https://learn.adafruit.com/pyportal-github-stars-trophy)
  polls `adafruit/circuitpython` every 60s from the device, for this reason.

## Verified end to end

Boot log, for comparison if yours misbehaves.

```
init panel on onboard ZIF (SPI0)
init AirLift on header SPI
nina-fw 3.3.0
wifi: connecting to <ssid>
wifi ok, rssi -48
mqtt: connecting
mqtt connected, subscribed to <user>/f/pr-queue
rx 1122 bytes
painting 1122 bytes
qr: 33 modules at 3px for https://github.com/mikeysklar/eink-pr-queue
refresh took 10.5s
idle
```

`rx` arrived from the `/get` replay, no producer run involved.
`payload-test.txt` on the board is an offline fixture.

## Libraries

```sh
circup install adafruit_esp32spi adafruit_connection_manager \
               adafruit_minimqtt adafruit_ticks neopixel adafruit_miniqr
```

`adafruit_epd`, `adafruit_framebuf` and `adafruit_bus_device` usually ship
already.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
