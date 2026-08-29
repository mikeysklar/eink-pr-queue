# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: GPL-3.0-or-later
"""PR queue on eInk, fed by Adafruit IO.

Feather RP2040 ThinkInk + AirLift FeatherWing on a doubler, driving a 4.2"
400x300 SSD1683 panel (#6381).

The two peripherals sit on SEPARATE SPI buses, which is the whole reason this
pairing works:

    panel   -> onboard 24-pin ZIF, SPI0, EPD_SCK/EPD_MOSI + EPD_CS/DC/RESET/BUSY
    AirLift -> Feather header SPI, SCK/MOSI/MISO + CS=D13, READY=D11, RESET=D12

Do NOT wire the panel to the header pins via EyeSPI. That layout puts the
panel's RST/BUSY on D11/D12, which is exactly where the AirLift's READY and
RESET live, and the two cannot coexist. The ribbon belongs in the board's own
ZIF socket.

Rendering goes through adafruit_epd (the framebuf path), NOT the displayio
SSD1683 driver. On a panel 256px or wider the displayio core switches command
0x44 to 2-byte column addressing, but on SSD168x 0x44 is byte-addressed, so
the X window collapses to ~8px and the rest of the panel is stale-RAM
speckle. adafruit_epd does not have that bug.

The payload is the finished screen, not data: N lines of at most 66 columns,
newline separated. A line starting with '~' is drawn white-on-black, and a
line starting with '@' is not a row at all: it carries a URL to render as a
QR code in the reserved bottom right block. All
layout lives in producer/render.py on the host, so changing the design never
means reflashing this board.

Mono, not 4-gray. The Grayscale4 class works on this panel, but shading the
risk levels in DARK and LIGHT was tried on real glass and read badly: the 6x8
font is too fine for the mid tones to stay crisp. It also halves the framebuf
cost, 15KB instead of 30KB.
"""

import os
import time

import board
import busio
import digitalio
import neopixel
import adafruit_miniqr

from adafruit_epd.epd import Adafruit_EPD
from adafruit_epd.ssd1683 import Adafruit_SSD1683
from adafruit_esp32spi import adafruit_esp32spi
import adafruit_connection_manager
import adafruit_minimqtt.adafruit_minimqtt as MQTT

# --- config -----------------------------------------------------------------

WIFI_SSID = os.getenv("WIFI_SSID")
WIFI_PASSWORD = os.getenv("WIFI_PASSWORD")
AIO_USERNAME = os.getenv("AIO_USERNAME")
AIO_KEY = os.getenv("AIO_KEY")
AIO_FEED = os.getenv("AIO_FEED", "pr-queue")

for _name in ("WIFI_SSID", "WIFI_PASSWORD", "AIO_USERNAME", "AIO_KEY"):
    if not os.getenv(_name):
        raise RuntimeError("settings.toml is missing " + _name)

FEED_TOPIC = "{}/f/{}".format(AIO_USERNAME, AIO_FEED)
GET_TOPIC = FEED_TOPIC + "/get"

PANEL_W, PANEL_H = 400, 300
GLYPH_W, GLYPH_H = 6, 8      # adafruit_framebuf built-in font, 6px advance
MAX_COLS = PANEL_W // GLYPH_W    # 66
MARGIN_X = 2

# A full 4.2" refresh is ~15s of flashing and the panel wants recovery time
# between them. Anything arriving inside this window is held, not dropped.
MIN_REFRESH_S = 180

# The QR block. 33 modules at 3px is 99px, and the host narrows the text on
# the rows it overlaps so the code keeps a real quiet zone. ECC Q (25%
# recovery) is chosen over L so the code still scans from a photo of a photo.
QR_X, QR_Y, QR_SCALE = 294, 192, 3

# --- status LED (so a headless panel can be diagnosed without a refresh) -----

_np_power = digitalio.DigitalInOut(board.NEOPIXEL_POWER)
_np_power.switch_to_output(value=True)
pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.15, auto_write=True)

RED, AMBER, BLUE, GREEN, OFF = (
    (60, 0, 0), (60, 30, 0), (0, 0, 60), (0, 60, 0), (0, 0, 0))


def status(color, message):
    pixel.fill(color)
    print(message)


# --- panel ------------------------------------------------------------------

status(AMBER, "init panel on onboard ZIF (SPI0)")
epd_spi = busio.SPI(board.EPD_SCK, board.EPD_MOSI)   # write-only, no MISO
display = Adafruit_SSD1683(
    PANEL_W, PANEL_H, epd_spi,
    cs_pin=digitalio.DigitalInOut(board.EPD_CS),
    dc_pin=digitalio.DigitalInOut(board.EPD_DC),
    sramcs_pin=None,                                  # ThinkInk has no SRAM chip
    rst_pin=digitalio.DigitalInOut(board.EPD_RESET),
    busy_pin=digitalio.DigitalInOut(board.EPD_BUSY),
)


def draw(payload):
    """Paint the panel from a finished text screen."""
    lines = payload.split("\n")
    # '@' lines are metadata, not rows. Pull them out before laying anything
    # out so they never consume a row or shift the grid.
    qr_url = None
    rows = []
    for line in lines:
        if line.startswith("@"):
            qr_url = line[1:]
        else:
            rows.append(line)
    lines = rows
    if not lines:
        return
    # Fit whatever we are sent: the host may send 21 airy lines or 30 dense
    # ones, and the device should not need reflashing to follow.
    pitch = min(14, max(GLYPH_H + 1, PANEL_H // len(lines)))
    lines = lines[: PANEL_H // pitch]

    display.fill(Adafruit_EPD.WHITE)
    for i, line in enumerate(lines):
        top = i * pitch
        inverse = line.startswith("~")
        if inverse:
            line = line[1:]
            display.fill_rect(0, top, PANEL_W, pitch, Adafruit_EPD.BLACK)
        ink = Adafruit_EPD.WHITE if inverse else Adafruit_EPD.BLACK
        text = line[:MAX_COLS]
        if text.strip():
            display.text(text, MARGIN_X, top + (pitch - GLYPH_H) // 2, ink, size=1)

    if qr_url:
        modules = draw_qr(qr_url)
        print("qr: {} modules at {}px for {}".format(modules, QR_SCALE, qr_url))

    started = time.monotonic()
    display.display()
    print("refresh took {:.1f}s".format(time.monotonic() - started))


def draw_qr(url):
    """Render `url` as a QR in the reserved bottom right block."""
    qr = adafruit_miniqr.QRCode(qr_type=None, error_correct=adafruit_miniqr.Q)
    qr.add_data(url.encode())
    qr.make()
    matrix = qr.matrix
    for y in range(matrix.height):
        for x in range(matrix.width):
            if matrix[x, y]:
                display.fill_rect(QR_X + x * QR_SCALE, QR_Y + y * QR_SCALE,
                                  QR_SCALE, QR_SCALE, Adafruit_EPD.BLACK)
    return matrix.width


def checksum(text):
    """FNV-1a, just to tell 'same screen' from 'new screen'. No hashlib here."""
    h = 2166136261
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
    return h


# --- radio ------------------------------------------------------------------

status(AMBER, "init AirLift on header SPI")
radio_spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
esp = adafruit_esp32spi.ESP_SPIcontrol(
    radio_spi,
    digitalio.DigitalInOut(board.D13),   # CS
    digitalio.DigitalInOut(board.D11),   # READY
    digitalio.DigitalInOut(board.D12),   # RESET
)
print("nina-fw", esp.firmware_version)


def connect_wifi():
    status(RED, "wifi: connecting to {}".format(WIFI_SSID))
    while not esp.is_connected:
        try:
            esp.connect_AP(WIFI_SSID, WIFI_PASSWORD)
        except (RuntimeError, ConnectionError) as err:
            print("  retry:", err)
            time.sleep(3)
    # Signal strength is a log line, not a feature, so it must never be able
    # to take down the boot. ap_info is None until the link is up, and the
    # attribute has moved between library versions.
    try:
        print("wifi ok, rssi", esp.ap_info.rssi)
    except (AttributeError, RuntimeError):
        print("wifi ok")


# --- state ------------------------------------------------------------------

pending = None        # newest payload not yet painted
last_drawn = None     # checksum of what is on the glass
last_refresh = -MIN_REFRESH_S


def on_message(client, topic, message):
    global pending
    print("rx {} bytes".format(len(message)))
    pending = message


def on_connect(client, userdata, flags, rc):
    client.subscribe(FEED_TOPIC)
    # Ask Adafruit IO to replay the feed's current value, so a device that
    # boots between pushes still paints instead of sitting blank.
    client.publish(GET_TOPIC, "\x00")
    status(BLUE, "mqtt connected, subscribed to " + FEED_TOPIC)


connect_wifi()

pool = adafruit_connection_manager.get_radio_socketpool(esp)
ssl_context = adafruit_connection_manager.get_radio_ssl_context(esp)
mqtt = MQTT.MQTT(
    broker="io.adafruit.com",
    port=8883,
    username=AIO_USERNAME,
    password=AIO_KEY,
    socket_pool=pool,
    ssl_context=ssl_context,
    is_ssl=True,
    keep_alive=120,
)
mqtt.on_connect = on_connect
mqtt.on_message = on_message

status(BLUE, "mqtt: connecting")
mqtt.connect()

while True:
    try:
        mqtt.loop(timeout=5)
    except (RuntimeError, OSError, MQTT.MMQTTException) as err:
        status(RED, "mqtt dropped: {!r}".format(err))
        time.sleep(5)
        try:
            if not esp.is_connected:
                connect_wifi()
            mqtt.reconnect()
            status(BLUE, "mqtt reconnected")
        except Exception as err:            # noqa: BLE001 - keep the panel alive
            print("  reconnect failed:", err)
        continue

    if pending is None:
        continue

    payload, pending = pending, None
    fingerprint = checksum(payload)
    if fingerprint == last_drawn:
        print("identical screen, not repainting")
        continue

    waited = time.monotonic() - last_refresh
    if waited < MIN_REFRESH_S:
        # Hold it rather than drop it, and paint as soon as the window opens.
        print("holding {:.0f}s for the refresh window".format(MIN_REFRESH_S - waited))
        pending = payload
        time.sleep(min(10, MIN_REFRESH_S - waited))
        continue

    status(AMBER, "painting {} bytes".format(len(payload)))
    draw(payload)
    last_drawn = fingerprint
    last_refresh = time.monotonic()
    status(GREEN, "idle")
