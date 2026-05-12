import serial, struct, sys
import numpy as np
from scipy.signal import medfilt, savgol_filter
import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets, QtCore

# ═══════════════════════════════════════════════════════════
#  CONFIG  — change these if needed
# ═══════════════════════════════════════════════════════════
SERIAL_PORT        = "/dev/ttyUSB0"   # Linux: /dev/ttyUSB0  Windows: COM3
BAUD_RATE          = 921600
SAMPLE_RATE        = 40_000          # must match ESP32
VREF               = 3.3
HISTORY_SECONDS    = 10
DISPLAY_SAMPLES    = 1000
PRE_TRIGGER        = 200
HYSTERESIS         = 0.15
# ═══════════════════════════════════════════════════════════

HISTORY_SIZE = SAMPLE_RATE * HISTORY_SECONDS
history      = np.zeros(HISTORY_SIZE, dtype=np.float64)
hist_idx     = 0

# ── Serial ────────────────────────────────────────────────
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.02)
except Exception as e:
    print(f"[ERROR] Cannot open {SERIAL_PORT}: {e}")
    print("Check your port and update SERIAL_PORT in the script.")
    sys.exit(1)

raw = bytearray()

def find_header(data):
    for i in range(len(data) - 1):
        if data[i] == 0xAA and data[i + 1] == 0x55:
            return i
    return -1

def clean(data):
    """
    Two-pass cleaning:
      1. Median filter  — kills single-sample ADC spikes
      2. Savitzky-Golay — smooth noise while preserving wave shape
    """
    if len(data) < 7:
        return data
    d = medfilt(data, kernel_size=3)          # kills impulse spikes
    d = savgol_filter(d, window_length=7, polyorder=3)  # smooth
    return d

def read_serial():
    global raw, history, hist_idx
    raw += ser.read(2048)
    while True:
        idx = find_header(raw)
        if idx == -1:
            raw = raw[-2:]
            break
        raw = raw[idx:]
        if len(raw) < 4:
            break
        count = struct.unpack_from('<H', raw, 2)[0]
        if count == 0 or count > 1024:
            raw = raw[2:]
            continue
        pkt = 4 + count * 2
        if len(raw) < pkt:
            break
        samples = struct.unpack_from(f'<{count}H', raw, 4)
        raw = raw[pkt:]

        volts = np.array(samples, dtype=np.float64) * (VREF / 4095.0)
        volts = clean(volts)

        n   = len(volts)
        end = hist_idx + n
        if end <= HISTORY_SIZE:
            history[hist_idx:end] = volts
        else:
            split = HISTORY_SIZE - hist_idx
            history[hist_idx:]  = volts[:split]
            history[:n - split] = volts[split:]
        hist_idx = end % HISTORY_SIZE

def get_latest(n):
    n     = min(n, HISTORY_SIZE)
    start = (hist_idx - n) % HISTORY_SIZE
    if start < hist_idx:
        return history[start:hist_idx].copy()
    return np.concatenate([history[start:], history[:hist_idx]])

def find_trigger(data, level, rising=True):
    armed = False
    for i in range(PRE_TRIGGER, len(data) - 1):
        if rising:
            if not armed and data[i] < (level - HYSTERESIS):
                armed = True
            if armed and data[i - 1] < level <= data[i]:
                return i
        else:
            if not armed and data[i] > (level + HYSTERESIS):
                armed = True
            if armed and data[i - 1] > level >= data[i]:
                return i
    return None

def measure_frequency(data):
    if len(data) < 64:
        return None
    n        = len(data)
    nfft     = n * 4
    windowed = (data - np.mean(data)) * np.hanning(n)
    fft      = np.abs(np.fft.rfft(windowed, n=nfft))
    freqs    = np.fft.rfftfreq(nfft, 1.0 / SAMPLE_RATE)
    fft[0]   = 0
    peak     = np.argmax(fft)
    return float(freqs[peak]) if peak > 0 else None

# ── UI ────────────────────────────────────────────────────
app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")

# Dark palette
pal = app.palette()
from pyqtgraph.Qt import QtGui
pal.setColor(QtGui.QPalette.ColorRole.Window,          QtGui.QColor(18, 18, 18))
pal.setColor(QtGui.QPalette.ColorRole.WindowText,      QtGui.QColor(200, 200, 200))
pal.setColor(QtGui.QPalette.ColorRole.Base,            QtGui.QColor(25, 25, 25))
pal.setColor(QtGui.QPalette.ColorRole.Button,          QtGui.QColor(35, 35, 35))
pal.setColor(QtGui.QPalette.ColorRole.ButtonText,      QtGui.QColor(200, 200, 200))
pal.setColor(QtGui.QPalette.ColorRole.Highlight,       QtGui.QColor(0, 180, 100))
app.setPalette(pal)

win = QtWidgets.QMainWindow()
win.setWindowTitle("ESP32 Oscilloscope")
win.resize(1350, 700)

central = QtWidgets.QWidget()
win.setCentralWidget(central)
vlay = QtWidgets.QVBoxLayout(central)
vlay.setContentsMargins(8, 6, 8, 6)
vlay.setSpacing(5)

# ── Controls ──────────────────────────────────────────────
ctrl = QtWidgets.QHBoxLayout()
ctrl.setSpacing(6)

def mk_spin(lo, hi, step, val, dec, w=70):
    s = QtWidgets.QDoubleSpinBox()
    s.setRange(lo, hi); s.setSingleStep(step)
    s.setValue(val);    s.setDecimals(dec)
    s.setFixedWidth(w)
    return s

def mk_label(txt):
    l = QtWidgets.QLabel(txt)
    l.setStyleSheet("color:#888;")
    return l

trig_spin  = mk_spin(0.0, VREF, 0.05, 1.65, 2)
hyst_spin  = mk_spin(0.01, 1.0, 0.05, HYSTERESIS, 2, 60)

edge_combo = QtWidgets.QComboBox()
edge_combo.addItems(["Rising ↑", "Falling ↓"])

disp_combo = QtWidgets.QComboBox()
for v in [256, 512, 1000, 2000, 4000]:
    disp_combo.addItem(str(v), v)
disp_combo.setCurrentIndex(2)

smooth_check = QtWidgets.QCheckBox("Smooth")
smooth_check.setChecked(False)
smooth_check.setToolTip("Apply Savitzky-Golay smoothing to reduce ADC noise")

auto_btn   = QtWidgets.QPushButton("⚡ Auto Level")
freeze_btn = QtWidgets.QPushButton("⏸  Freeze")
freeze_btn.setCheckable(True)

status_lbl = QtWidgets.QLabel("● Live")
status_lbl.setStyleSheet("color:#00dd77; font-weight:bold; font-size:13px;")

for lbl, wgt in [
    ("Trig V:",     trig_spin),
    ("Edge:",       edge_combo),
    ("Samples:",    disp_combo),
    ("Hysteresis:", hyst_spin),
]:
    ctrl.addWidget(mk_label(lbl))
    ctrl.addWidget(wgt)
    ctrl.addSpacing(8)

ctrl.addWidget(smooth_check)
ctrl.addSpacing(8)
ctrl.addWidget(auto_btn)
ctrl.addWidget(freeze_btn)
ctrl.addStretch()
ctrl.addWidget(status_lbl)
vlay.addLayout(ctrl)

# ── Plot ──────────────────────────────────────────────────
pw = pg.PlotWidget(background='#0a0a0a')
pw.setLabel('left',   'Voltage', units='V',  **{'color':'#aaa','font-size':'11pt'})
pw.setLabel('bottom', 'Time',    units='s',  **{'color':'#aaa','font-size':'11pt'})
pw.setYRange(-0.1, VREF + 0.1)
pw.showGrid(x=True, y=True, alpha=0.15)
pw.getAxis('left').setTextPen('#aaa')
pw.getAxis('bottom').setTextPen('#aaa')
vlay.addWidget(pw, stretch=1)

curve = pw.plot(pen=pg.mkPen('#00FF88', width=1.5))

trig_line = pg.InfiniteLine(
    pos=trig_spin.value(), angle=0, movable=True,
    pen=pg.mkPen('#ff5555', width=1,
                 style=QtCore.Qt.PenStyle.DashLine),
    label='  Trig {value:.2f}V',
    labelOpts={'color':'#ff5555', 'position':0.03,
               'fill': pg.mkBrush(0,0,0,120)}
)
pw.addItem(trig_line)

hyst_hi = pg.InfiniteLine(pos=trig_spin.value() + HYSTERESIS, angle=0,
    pen=pg.mkPen('#ff555530', width=1))
hyst_lo = pg.InfiniteLine(pos=trig_spin.value() - HYSTERESIS, angle=0,
    pen=pg.mkPen('#ff555530', width=1))
pw.addItem(hyst_hi)
pw.addItem(hyst_lo)

def update_hyst_lines(v=None):
    v = v if v is not None else trig_spin.value()
    h = hyst_spin.value()
    hyst_hi.setValue(v + h)
    hyst_lo.setValue(v - h)

trig_line.sigPositionChanged.connect(
    lambda l: (trig_spin.blockSignals(True),
               trig_spin.setValue(round(l.value(), 2)),
               trig_spin.blockSignals(False),
               update_hyst_lines(l.value()))
)
trig_spin.valueChanged.connect(lambda v: (trig_line.setValue(v), update_hyst_lines(v)))
hyst_spin.valueChanged.connect(lambda _: update_hyst_lines())

# ── Stats bar ─────────────────────────────────────────────
stats_row = QtWidgets.QHBoxLayout()
sl = {}
for key, default in [
    ('vmin','Vmin: —'), ('vmax','Vmax: —'), ('vpp','Vpp: —'),
    ('freq','Freq: —'), ('dc','DC: —'),     ('trig','Trig: —')
]:
    lbl = QtWidgets.QLabel(default)
    lbl.setStyleSheet(
        "font-family:monospace; font-size:11px; color:#aaa;"
        "background:#141414; padding:3px 10px; border-radius:3px;"
    )
    sl[key] = lbl
    stats_row.addWidget(lbl)
    stats_row.addSpacing(4)
stats_row.addStretch()
vlay.addLayout(stats_row)

# Auto level
def auto_level():
    data = get_latest(DISPLAY_SAMPLES)
    if len(data) > 10:
        mid = float((np.min(data) + np.max(data)) / 2.0)
        trig_spin.setValue(round(mid, 2))

auto_btn.clicked.connect(auto_level)

# ── Main update ───────────────────────────────────────────
def update():
    read_serial()

    if freeze_btn.isChecked():
        status_lbl.setText("⏸  Frozen")
        status_lbl.setStyleSheet("color:#f0a500; font-weight:bold; font-size:13px;")
        return

    status_lbl.setText("● Live")
    status_lbl.setStyleSheet("color:#00dd77; font-weight:bold; font-size:13px;")

    disp_n = disp_combo.currentData()
    trig_v = trig_spin.value()
    rising = edge_combo.currentIndex() == 0

    search_n = disp_n + PRE_TRIGGER + 1024
    data     = get_latest(min(search_n, HISTORY_SIZE))

    if len(data) < disp_n:
        return

    trig_idx = find_trigger(data, trig_v, rising)

    if trig_idx is not None and (trig_idx - PRE_TRIGGER) >= 0 \
            and (trig_idx - PRE_TRIGGER + disp_n) <= len(data):
        start   = trig_idx - PRE_TRIGGER
        display = data[start : start + disp_n].copy()
        trig_ok = True
    else:
        display = data[-disp_n:].copy()
        trig_ok = False

    # Optional extra smoothing on display
    if smooth_check.isChecked() and len(display) >= 7:
        display = savgol_filter(display, window_length=7, polyorder=3)

    t = np.arange(len(display)) / SAMPLE_RATE
    curve.setData(t, display)

    # Measurements
    mn   = float(np.min(display))
    mx   = float(np.max(display))
    dc   = float(np.mean(display))
    freq = measure_frequency(display)

    sl['vmin'].setText(f"Vmin: {mn:.3f} V")
    sl['vmax'].setText(f"Vmax: {mx:.3f} V")
    sl['vpp'].setText( f"Vpp:  {mx - mn:.3f} V")
    sl['freq'].setText(f"Freq: {freq:.1f} Hz" if freq else "Freq: —")
    sl['dc'].setText(  f"DC:   {dc:.3f} V")
    sl['trig'].setText(
        ("✔ Triggered" if trig_ok else "~ Free-run") +
        f" @ {trig_v:.2f}V ({'↑' if rising else '↓'})"
    )

timer = QtCore.QTimer()
timer.timeout.connect(update)
timer.start(16)   # 60 fps

win.show()
sys.exit(app.exec())
