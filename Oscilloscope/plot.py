import serial, struct, sys
import numpy as np
from scipy.signal import medfilt, savgol_filter
import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets, QtCore

# ═══════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════
SERIAL_PORT        = "/dev/ttyACM0"  # Native USB CDC port on Linux
BAUD_RATE          = 921600          # Ignored by STM32 CDC, but required by PySerial
SAMPLE_RATE        = 40_000          # Matches the STM32 interval
VREF               = 3.3             # STM32 ADC reference voltage
HISTORY_SECONDS    = 10
DISPLAY_SAMPLES    = 1000
PRE_TRIGGER        = 200
HYSTERESIS         = 0.15
EXPECTED_COUNT     = 256             # HALF_BUF_LEN from firmware - exact match required
# ═══════════════════════════════════════════════════════════

HISTORY_SIZE = SAMPLE_RATE * HISTORY_SECONDS

ch1_history = np.zeros(HISTORY_SIZE, dtype=np.float64)
ch2_history = np.zeros(HISTORY_SIZE, dtype=np.float64)
hist_idx    = 0

# Diagnostics so you can see if corruption is actually happening
stats_counters = {"packets_ok": 0, "packets_rejected": 0, "bytes_skipped": 0}

try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.02)
except Exception as e:
    print(f"[ERROR] Cannot open {SERIAL_PORT}: {e}")
    print("Hint: Check 'ls /dev/ttyA*' to ensure the STM32 enumerated properly.")
    sys.exit(1)

raw = bytearray()

def find_header(data):
    for i in range(len(data) - 1):
        if data[i] == 0xAA and data[i + 1] == 0x55:
            return i
    return -1

def checksum16(data: bytes) -> int:
    # Must match the firmware's checksum16(): plain 16-bit additive sum,
    # wrapping the same way a uint16_t does on the MCU.
    return sum(data) & 0xFFFF

DEBUG_DUMPS_REMAINING = 5  # print this many rejected packets in detail, then stop

def read_serial():
    global raw, hist_idx, ch1_history, ch2_history, DEBUG_DUMPS_REMAINING
    raw += ser.read(8192)

    while True:
        idx = find_header(raw)
        if idx == -1:
            # No header anywhere in the buffer. Keep only the last byte in
            # case it's the first half of a header split across reads.
            stats_counters["bytes_skipped"] += max(0, len(raw) - 1)
            raw = raw[-1:]
            break

        if idx > 0:
            stats_counters["bytes_skipped"] += idx
        raw = raw[idx:]

        # Header(4) + count*2 data bytes + 2 checksum bytes
        if len(raw) < 4:
            break

        count = struct.unpack_from('<H', raw, 2)[0]

        # Reject anything that isn't exactly the packet size the firmware
        # sends. Previously this accepted any 0 < count <= 1024, which let
        # misaligned/corrupted "packets" through to the plot.
        if count != EXPECTED_COUNT:
            stats_counters["packets_rejected"] += 1
            if DEBUG_DUMPS_REMAINING > 0:
                DEBUG_DUMPS_REMAINING -= 1
                print(f"[REJECT-COUNT] count={count} (expected {EXPECTED_COUNT}) "
                      f"header_bytes={raw[0:4].hex()} idx_in_buffer={idx}")
            raw = raw[2:]          # drop just the false header, keep searching
            continue

        pkt = 4 + count * 2 + 2    # +2 for the trailing checksum
        if len(raw) < pkt:
            break                  # wait for the rest of the packet to arrive

        payload = bytes(raw[4:4 + count * 2])
        expected_chk = struct.unpack_from('<H', raw, 4 + count * 2)[0]
        actual_chk = checksum16(payload)

        if actual_chk != expected_chk:
            # Corrupted/misaligned packet - this is the case that used to
            # produce the impossible 50V+ spikes. Discard it instead of
            # plotting it.
            stats_counters["packets_rejected"] += 1
            if DEBUG_DUMPS_REMAINING > 0:
                DEBUG_DUMPS_REMAINING -= 1
                print(f"[REJECT-CHK] expected={expected_chk:04x} actual={actual_chk:04x} "
                      f"count={count} first8={payload[:8].hex()} last8={payload[-8:].hex()} "
                      f"trailing_bytes_at_chk_pos={raw[4+count*2:4+count*2+2].hex()}")
            raw = raw[2:]
            continue

        samples = struct.unpack(f'<{count}H', payload)
        raw = raw[pkt:]
        stats_counters["packets_ok"] += 1

        # Convert to volts (STM32 uses 12-bit ADC -> 4095)
        volts = np.array(samples, dtype=np.float64) * (VREF / 4095.0)

        # Split the interleaved data (CH1, CH2, CH1, CH2...)
        # No filtering here - keep ingestion cheap so we never fall behind
        # the 40kHz stream. Smoothing (if wanted) happens only on the small
        # slice that actually gets drawn, in update().
        v1 = volts[0::2]
        v2 = volts[1::2]

        n = len(v1)
        if n == 0:
            continue

        end = hist_idx + n
        if end <= HISTORY_SIZE:
            ch1_history[hist_idx:end] = v1
            ch2_history[hist_idx:end] = v2
        else:
            split = HISTORY_SIZE - hist_idx
            ch1_history[hist_idx:]  = v1[:split]
            ch1_history[:n - split] = v1[split:]
            ch2_history[hist_idx:]  = v2[:split]
            ch2_history[:n - split] = v2[split:]
        hist_idx = end % HISTORY_SIZE

def get_latest(history_arr, n):
    n = min(n, HISTORY_SIZE)
    start = (hist_idx - n) % HISTORY_SIZE
    if start < hist_idx:
        return history_arr[start:hist_idx].copy()
    return np.concatenate([history_arr[start:], history_arr[:hist_idx]])

def smooth_for_display(data):
    # Light smoothing applied only to the slice we're about to draw
    # (~1000-4000 points, once per GUI frame) - cheap compared to running
    # it on every 128-sample packet at 300+ Hz like before.
    if len(data) < 7:
        return data
    d = medfilt(data, kernel_size=3)
    return savgol_filter(d, window_length=7, polyorder=3)

def find_trigger(data, level, rising=True):
    armed = False
    for i in range(PRE_TRIGGER, len(data) - 1):
        if rising:
            if not armed and data[i] < (level - HYSTERESIS): armed = True
            if armed and data[i - 1] < level <= data[i]: return i
        else:
            if not armed and data[i] > (level + HYSTERESIS): armed = True
            if armed and data[i - 1] > level >= data[i]: return i
    return None

def measure_frequency(data):
    if len(data) < 64: return None
    n = len(data)
    windowed = (data - np.mean(data)) * np.hanning(n)
    fft = np.abs(np.fft.rfft(windowed, n=n * 4))
    freqs = np.fft.rfftfreq(n * 4, 1.0 / SAMPLE_RATE)
    fft[0] = 0
    peak = np.argmax(fft)
    return float(freqs[peak]) if peak > 0 else None

# ── UI Setup ──────────────────────────────────────────────
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")

pal = app.palette()
from pyqtgraph.Qt import QtGui
pal.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(18, 18, 18))
pal.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(200, 200, 200))
pal.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(25, 25, 25))
pal.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(35, 35, 35))
pal.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(200, 200, 200))
app.setPalette(pal)

win = QtWidgets.QMainWindow()
win.setWindowTitle("STM32 Dual-Channel Sync Scope (40kHz)")
win.resize(1350, 700)

central = QtWidgets.QWidget()
win.setCentralWidget(central)
vlay = QtWidgets.QVBoxLayout(central)

# ── Controls ──────────────────────────────────────────────
ctrl = QtWidgets.QHBoxLayout()

def mk_spin(lo, hi, step, val):
    s = QtWidgets.QDoubleSpinBox()
    s.setRange(lo, hi); s.setSingleStep(step); s.setValue(val)
    return s

src_combo  = QtWidgets.QComboBox(); src_combo.addItems(["CH1 (Yellow)", "CH2 (Cyan)"])
trig_spin  = mk_spin(0.0, VREF, 0.05, 1.65)
edge_combo = QtWidgets.QComboBox(); edge_combo.addItems(["Rising ↑", "Falling ↓"])
disp_combo = QtWidgets.QComboBox()
for v in [256, 512, 1000, 2000, 4000]: disp_combo.addItem(str(v), v)
disp_combo.setCurrentIndex(2)

status_lbl = QtWidgets.QLabel("● Live")
status_lbl.setStyleSheet("color:#00dd77; font-weight:bold;")

link_lbl = QtWidgets.QLabel("")
link_lbl.setStyleSheet("color:#888; font-family:monospace; font-size:11px;")

for lbl, wgt in [("Trigger Src:", src_combo), ("Level V:", trig_spin), ("Edge:", edge_combo), ("Samples:", disp_combo)]:
    l = QtWidgets.QLabel(lbl); l.setStyleSheet("color:#888;")
    ctrl.addWidget(l); ctrl.addWidget(wgt); ctrl.addSpacing(8)

ctrl.addStretch()
ctrl.addWidget(link_lbl)
ctrl.addSpacing(12)
ctrl.addWidget(status_lbl)
vlay.addLayout(ctrl)

# ── Plot ──────────────────────────────────────────────────
pw = pg.PlotWidget(background='#0a0a0a')
pw.setYRange(-0.1, VREF + 0.1)
pw.showGrid(x=True, y=True, alpha=0.15)
vlay.addWidget(pw, stretch=1)

curve1 = pw.plot(pen=pg.mkPen('#FFDD00', width=1.5))
curve2 = pw.plot(pen=pg.mkPen('#00FFFF', width=1.5))
trig_line = pg.InfiniteLine(pos=1.65, angle=0, movable=True, pen=pg.mkPen('#ff5555', style=QtCore.Qt.PenStyle.DashLine))
pw.addItem(trig_line)

trig_line.sigPositionChanged.connect(lambda l: trig_spin.setValue(round(l.value(), 2)))
trig_spin.valueChanged.connect(lambda v: trig_line.setValue(v))

# ── Stats bar ─────────────────────────────────────────────
stats_vlay = QtWidgets.QVBoxLayout()
stats = {}
for ch_name, color, key_prefix in [("CH1", "#FFDD00", "c1_"), ("CH2", "#00FFFF", "c2_")]:
    row = QtWidgets.QHBoxLayout()
    name_lbl = QtWidgets.QLabel(ch_name)
    name_lbl.setStyleSheet(f"font-weight:bold; color:{color}; padding:2px;")
    name_lbl.setFixedWidth(35)
    row.addWidget(name_lbl)

    for key, default in [('vmin','Vmin: —'), ('vmax','Vmax: —'), ('vpp','Vpp: —'),
                         ('freq','Freq: —'), ('dc','DC: —')]:
        lbl = QtWidgets.QLabel(default)
        lbl.setStyleSheet("font-family:monospace; font-size:11px; color:#aaa; background:#141414; padding:3px 10px; border-radius:3px;")
        stats[key_prefix + key] = lbl
        row.addWidget(lbl)
        row.addSpacing(4)
    row.addStretch()
    stats_vlay.addLayout(row)
vlay.addLayout(stats_vlay)

# ── Main update ───────────────────────────────────────────
def update():
    read_serial()
    disp_n = disp_combo.currentData()
    trig_v = trig_spin.value()
    rising = edge_combo.currentIndex() == 0

    ok = stats_counters["packets_ok"]
    rej = stats_counters["packets_rejected"]
    link_lbl.setText(f"pkts ok:{ok} rejected:{rej}")

    search_n  = disp_n + PRE_TRIGGER + 1024

    # Grab aligned data for both
    d1 = get_latest(ch1_history, search_n)
    d2 = get_latest(ch2_history, search_n)

    if len(d1) < disp_n: return

    trig_data = d1 if src_combo.currentIndex() == 0 else d2
    trig_idx = find_trigger(trig_data, trig_v, rising)

    if trig_idx is not None and (trig_idx - PRE_TRIGGER) >= 0 and (trig_idx - PRE_TRIGGER + disp_n) <= len(d1):
        start = trig_idx - PRE_TRIGGER
        plot_d1 = d1[start : start + disp_n]
        plot_d2 = d2[start : start + disp_n]
    else:
        plot_d1 = d1[-disp_n:]
        plot_d2 = d2[-disp_n:]

    plot_d1 = smooth_for_display(plot_d1)
    plot_d2 = smooth_for_display(plot_d2)

    t = np.arange(len(plot_d1)) / SAMPLE_RATE
    curve1.setData(t, plot_d1)
    curve2.setData(t, plot_d2)

    # Update Stats
    for plot_d, prefix in [(plot_d1, "c1_"), (plot_d2, "c2_")]:
        if len(plot_d) == 0: continue
        mn, mx, dc = float(np.min(plot_d)), float(np.max(plot_d)), float(np.mean(plot_d))
        freq = measure_frequency(plot_d)
        stats[prefix+'vmin'].setText(f"Vmin: {mn:.3f} V")
        stats[prefix+'vmax'].setText(f"Vmax: {mx:.3f} V")
        stats[prefix+'vpp'].setText(f"Vpp:  {mx - mn:.3f} V")
        stats[prefix+'freq'].setText(f"Freq: {freq:.1f} Hz" if freq else "Freq: —")
        stats[prefix+'dc'].setText(f"DC:   {dc:.3f} V")

timer = QtCore.QTimer()
timer.timeout.connect(update)
timer.start(16)

win.show()
sys.exit(app.exec())
