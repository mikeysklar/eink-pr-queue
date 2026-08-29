# eink-pr-queue

![The rig: 4.2 inch eInk panel showing the PR queue, driven by a Feather RP2040 ThinkInk and an AirLift FeatherWing on a doubler](pics/eink-pr-queue.jpeg)

A Feather RP2040 ThinkInk and AirLift paint a GitHub pull request risk
dashboard onto 4.2 inch eInk via Adafruit IO.

## How it connects

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  YOUR MAC                                                        │
  │                                                                  │
  │  collect.py ──▶ score.py ──▶ render.py ──▶ push.py               │
  │   gh pr list    5 concerns   66x21 text    HTTPS POST            │
  └───────────────────────────────────────────────────┬──────────────┘
                                                      │ {"value": "..."}
                                                      ▼
                        https://io.adafruit.com/api/v2/webhooks/feed/<token>
                                                      │
                                           ┌──────────┴──────────┐
                                           │  feed: pr-queue     │
                                           │  history: OFF       │
                                           └──────────┬──────────┘
                                                      │ MQTT/TLS :8883
                                                      │ <user>/f/pr-queue
                                                      ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  FEATHER DOUBLER                                                 │
  │                                                                  │
  │  ┌────────────────────────┐      ┌────────────────────────────┐  │
  │  │ Feather RP2040 ThinkInk│      │ AirLift FeatherWing        │  │
  │  │                        │◀────▶│ ESP32 co-processor         │  │
  │  │  CircuitPython 10.3    │ hdr  │ nina-fw 3.3.0              │  │
  │  │                        │ SPI  │                            │  │
  │  │  SCK  GPIO14 ──────────┼──────┼──▶ SCK                     │  │
  │  │  MOSI GPIO15 ──────────┼──────┼──▶ MOSI                    │  │
  │  │  MISO GPIO8  ◀─────────┼──────┼─── MISO                    │  │
  │  │  D13         ──────────┼──────┼──▶ CS     (also board.LED) │  │
  │  │  D11         ◀─────────┼──────┼─── READY                   │  │
  │  │  D12         ──────────┼──────┼──▶ RESET                   │  │
  │  └───────────┬────────────┘      └────────────────────────────┘  │
  └──────────────┼───────────────────────────────────────────────────┘
                 │ onboard 24-pin ZIF, SPI0, a SEPARATE bus
                 │ EPD_SCK/MOSI GPIO22/23 + CS/DC/RESET/BUSY GPIO16-19
                 ▼
        ┌─────────────────────────────┐
        │  4.2" 400x300 SSD1683       │
        │  #6381, FPC-190 ribbon      │
        │  66 cols x 21 rows, 8s draw │
        └─────────────────────────────┘
```

The webhook is the way in and MQTT is the way out. The panel cannot receive an
inbound HTTP push, so it subscribes instead. Adafruit IO's outbound Actions
are not used.

## The one wiring rule

**The panel goes in the ThinkInk's own ZIF socket.** Wiring it to the header
via EyeSPI puts its RST and BUSY on D11 and D12, which is exactly where the
AirLift's READY and RESET live. In the ZIF socket the panel sits on SPI0 and
the radio has the header bus to itself, so the two never touch.

## Producer

Pure stdlib Python plus the `gh` CLI. Nothing to install.

```sh
cd producer
DRY_RUN=1 ./update.sh adafruit/circuitpython --since-days 21   # render only
./update.sh adafruit/circuitpython --limit 200                 # render and push
```

| Step | Does | Judgment? |
|---|---|---|
| `collect.py` | `gh pr list` into `raw.json` | none, facts only |
| `score.py` | five concerns into `rows.json` | three of five, mechanically |
| `render.py` | the exact 66x21 panel text | layout only |
| `push.py` | POST to the feed webhook | none |

### What the scoring does and does not do

Each PR is scored on five concerns and takes the **worst, never the average**.
Three are mechanical. Two are not:

- **APIs / interfaces** always comes out `null`. Deciding whether a PR reuses
  the established convention needs the diff read against recently merged work
  in the same area. No regex does that.
- **Single problem** only reaches `med`, via a weak proxy (a diff fanning
  across many top-level trees).

Size and tested also cap at `med` on purpose: a big diff for a big well-scoped
problem is not a flag, and a missing test claim on a two-line change is barely
one. **High comes from core-path blast radius, or from a judgment a human or
model supplied.** To supply one, edit `rows.json`, set `concerns.apis` or
`concerns.single` to 0/1/2, and recompute:

```sh
./score.py .work/rows.json --rescore -o .work/rows.json
```

Core paths per repo live in `CORE_PATHS` in `score.py`. For an unlisted repo,
pass `--core-path py/ --core-path shared-bindings/` or the concern degrades to
a plain file count and says so.

### Feed setup, and the thing that will bite you

Create a feed, then **turn history OFF** (Feed > Feed Info > History).

| History | Max bytes per value |
|---|---|
| on | 1,024 |
| off | 524,288, per the feed's own history dialog |

Adafruit's two dialogs disagree on the off figure: the history dialog says
512KB (524288 bytes), the webhook dialog says 100 kilobytes. Either is ample
here. The number that actually matters is the 1,024 while history is ON,
because a full 21-row screen is 1.0 to 1.4KB and every push fails against it.

Nothing is lost by turning history off: only the newest value is ever drawn,
and MQTT `/get` returns it on demand. Then:

```sh
export AIO_WEBHOOK_URL="https://io.adafruit.com/api/v2/webhooks/feed/XXXXXXXX"
```

The token is in the URL, so no API key travels with the request. `push.py`
hashes the last payload and skips an identical push.

## Wire format

The payload is **the finished screen**, not data. All layout happens on the
host.

```
66 columns x 21 rows, newline separated, trailing spaces stripped
a line beginning with '~' is drawn white-on-black (the title bar)
```

66x21 is what 400x300 gives you with the `adafruit_epd` built-in 6x8 font,
using 396x294 of the panel. Doing it this way means every layout change is a
host-side edit with no reflash.

Row anatomy:

```
>*#10283 rianadon       492d  13f    +517/-1 HIGH changes req. CI!
||
|+-- '*' stale: no maintainer review past --stale-days (default 180)
+--- '>' overall risk High
```

## Notes from building it

**Mono, not 4-gray.** The `Adafruit_SSD1683_Grayscale4` class works and shading
each risk level a different tone was tried on real glass. It read badly: the
6x8 font is too fine for DARK and LIGHT to stay crisp. Mono also halves the
framebuf, 15KB instead of 30KB, and draws in 8.0s instead of 10.0s.

**adafruit_epd, not displayio.** The displayio SSD1683 path has a bug on panels
256px or wider: the core switches command 0x44 to 2-byte column addressing, but
on SSD168x 0x44 is byte-addressed, so the X window collapses to about 8px and
the rest is stale-RAM speckle. The framebuf path does not have it.

**RAM is not the constraint.** 67KB free of 264KB with every import loaded and
the framebuf allocated.

**The NeoPixel is the status display.** A wall panel cannot report for itself
without burning an 8 second refresh, so the LED carries state: amber
initialising or painting, red WiFi or MQTT down, blue connected, green idle
and current.

## Libraries

```sh
circup install adafruit_esp32spi adafruit_connection_manager \
               adafruit_minimqtt adafruit_ticks neopixel
```

`adafruit_epd`, `adafruit_framebuf` and `adafruit_bus_device` ship with most
ThinkInk setups already.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
