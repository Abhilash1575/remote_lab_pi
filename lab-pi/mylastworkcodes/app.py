#!/usr/bin/env python3
import sys

# Import lgpio FIRST before eventlet patches anything!
try:
    import lgpio
    RELAY_PIN = 26
except Exception as e:
    print(f"lgpio import failed: {e}")
    lgpio = None
    RELAY_PIN = None

import eventlet
eventlet.monkey_patch()

import os, time, subprocess, threading, queue, tempfile, re, random, json, math, asyncio
from flask import Flask, send_from_directory, request, jsonify, render_template, abort
from flask_socketio import SocketIO, emit

# Optional: serial usage guarded (so app still runs if pyserial not available)
try:
    import serial
    from serial.tools import list_ports
except Exception as e:
    serial = None
    list_ports = None

from werkzeug.utils import secure_filename

# ---------- CONFIG ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # base path relative to script location
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
DEFAULT_FW_DIR = os.path.join(BASE_DIR, 'default_fw')  # contains esp32_default.bin etc
SOP_DIR = os.path.join(BASE_DIR, 'static')      # contains exp.pdf
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DEFAULT_FW_DIR, exist_ok=True)
os.makedirs(SOP_DIR, exist_ok=True)

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = 'devkey'
socketio = SocketIO(app, async_mode='eventlet')

# Global active sessions for authorization
active_sessions = {}

serial_lock = threading.Lock()
ser = None
ser_stop = threading.Event()
data_generator_thread = None  # global mock generator

# ---------- RELAY CONTROL ----------
# Keep a persistent handle to GPIO chip to prevent issues with repeated calls
gpio_handle = None

def init_gpio():
    """Initialize GPIO chip handle"""
    global gpio_handle
    if lgpio is None or RELAY_PIN is None:
        return False
    try:
        if gpio_handle is None:
            gpio_handle = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_output(gpio_handle, RELAY_PIN)
        return True
    except Exception as e:
        print(f"Error initializing GPIO: {e}")
        gpio_handle = None
        return False

def relay_on():
    """Turn the relay ON (power supply to experiments)"""
    if not init_gpio():
        return False
    try:
        lgpio.gpio_write(gpio_handle, RELAY_PIN, 0)  # Most relay modules are ACTIVE LOW
        print("Relay ON")
        return True
    except Exception as e:
        print(f"Error turning relay ON: {e}")
        return False

def relay_off():
    """Turn the relay OFF (power supply to experiments off)"""
    if not init_gpio():
        return False
    try:
        lgpio.gpio_write(gpio_handle, RELAY_PIN, 1)  # Most relay modules are ACTIVE LOW
        print("Relay OFF")
        return True
    except Exception as e:
        print(f"Error turning relay OFF: {e}")
        return False

# ---------- UTIL ----------
def list_serial_ports():
    if list_ports is None:
        return []
    return [p.device for p in list_ports.comports()]

@app.route('/')
def index():
    return render_template('homepage.html')

@app.route('/experiment')
def experiment():
    session_key = request.args.get('key')
    if not session_key:
        return render_template('expired_session.html')

    # For testing purposes: if session key is 'testpop123', create a temporary session
    if session_key == 'testpop123' and session_key not in active_sessions:
        active_sessions[session_key] = {
            'start_time': time.time(),
            'duration': 0.08,  # 5 seconds
            'expires_at': time.time() + (0.08 * 60)
        }

    # Clean up expired sessions
    current_time = time.time()
    expired_keys = [k for k, v in active_sessions.items() if current_time > v['expires_at']]
    for k in expired_keys:
        del active_sessions[k]
        # Turn relay OFF when session expires
        relay_off()

    if session_key not in active_sessions:
        return render_template('expired_session.html')

    duration = active_sessions[session_key]['duration']
    session_end_time = int(active_sessions[session_key]['expires_at'] * 1000)  # JS milliseconds
    return render_template('index.html', session_duration=duration, session_end_time=session_end_time)

@app.route('/add_session', methods=['POST'])
def add_session():
    data = request.get_json()
    session_key = data.get('session_key')
    duration = data.get('duration', 5)
    if session_key:
        active_sessions[session_key] = {
            'start_time': time.time(),
            'duration': duration,
            'expires_at': time.time() + (duration * 60)
        }
        # Do NOT turn relay ON automatically when session starts
    return jsonify({'status': 'added'})

@app.route('/remove_session', methods=['POST'])
def remove_session():
    data = request.get_json()
    session_key = data.get('session_key')
    if session_key in active_sessions:
        del active_sessions[session_key]
        # Turn relay OFF automatically when session is removed to save power
        relay_off()
    return jsonify({'status': 'removed'})

@app.route('/toggle_relay', methods=['POST'])
def toggle_relay():
    """Toggle the relay ON or OFF based on request data"""
    data = request.get_json()
    state = data.get('state')
    session_key = data.get('session_key')
    
    # Check if session is valid
    if session_key not in active_sessions:
        return jsonify({'status': 'error', 'message': 'Invalid session'}), 400
    
    if state == 'on':
        success = relay_on()
        return jsonify({'status': 'on' if success else 'error'})
    elif state == 'off':
        success = relay_off()
        return jsonify({'status': 'off' if success else 'error'})
    else:
        return jsonify({'status': 'error', 'message': 'Invalid state'}), 400

@app.route('/chart')
def chart():
    # Clean up expired sessions
    current_time = time.time()
    expired_keys = [k for k, v in active_sessions.items() if current_time > v['expires_at']]
    for k in expired_keys:
        del active_sessions[k]
        # Turn relay OFF when session expires
        relay_off()

    session_key = request.args.get('key')
    if not session_key or session_key not in active_sessions:
        return render_template('expired_session.html')
    return render_template('chart.html')

@app.route('/camera')
def camera():
    # Clean up expired sessions
    current_time = time.time()
    expired_keys = [k for k, v in active_sessions.items() if current_time > v['expires_at']]
    for k in expired_keys:
        del active_sessions[k]
        # Turn relay OFF when session expires
        relay_off()

    session_key = request.args.get('key')
    if not session_key or session_key not in active_sessions:
        return render_template('expired_session.html')
    return render_template('camera.html')

@app.route('/homepage')
def homepage():
    return render_template('homepage.html')

@app.route('/ports')
def ports_rest():
    return jsonify({'ports': list_serial_ports()})

# ---------- FLASH ----------
@app.route('/flash', methods=['POST'])
def flash():
    board = request.form.get('board', 'generic')
    port = request.form.get('port', '') or ''
    available_ports = list_serial_ports()
    default_port = available_ports[0] if available_ports else '/dev/ttyUSB0'
    port = port or default_port
    fw = request.files.get('firmware')
    if not fw:
        return jsonify({'status': 'No firmware uploaded'}), 400
    fname = secure_filename(fw.filename)
    dest = os.path.join(UPLOAD_DIR, fname)
    fw.save(dest)

    commands = {
        'esp32': f"esptool.py --chip esp32 --port {port} write_flash 0x10000 {dest}",
        'esp8266': f"esptool.py --port {port} write_flash 0x00000 {dest}",
        'arduino': f"avrdude -v -p atmega328p -c arduino -P {port} -b115200 -D -U flash:w:{dest}:i",
        'attiny': f"avrdude -v -p attiny85 -c usbasp -P {port} -U flash:w:{dest}:i",
        'stm32': f"openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c \"program {dest} 0x08000000 verify reset exit\"",
        'nucleo_f446re': f"openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c \"program {dest} 0x08000000 verify reset exit\"",
        'black_pill': f"openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c \"program {dest} 0x08000000 verify reset exit\"",
        'msp430': f"mspdebug rf2500 'prog {dest}'",
        'tiva': f"openocd -f board/ti_ek-tm4c123gxl.cfg -c \"program {dest} verify reset exit\"",
        'tms320f28377s': f"python3 dsp/flash_tool.py {dest}",
        'generic': f"echo 'No flashing command configured for {board}. Uploaded to {dest}'"
    }

    cmd = commands.get(board, commands['generic'])
    socketio.start_background_task(run_flash_command, cmd, fname)
    return jsonify({'status': f'Flashing started for {board}', 'command': cmd})

def run_flash_command(cmd, filename=None):
    try:
        socketio.emit('flashing_status', f"Starting: {cmd}")
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in iter(p.stdout.readline, ''):
            if line is None:
                continue
            socketio.emit('flashing_status', line.strip())
        p.wait()
        rc = p.returncode
        msg = '✅ Flashing completed successfully' if rc == 0 else f'⚠️ Flashing ended with return code {rc}'
        socketio.emit('flashing_status', f'{msg} (file: {filename})')
    except Exception as e:
        socketio.emit('flashing_status', f'Error while flashing: {e}')

# ---------- FACTORY RESET ENDPOINT ----------
# Expects JSON or form { "board": "esp32" }
# Finds corresponding default firmware file under DEFAULT_FW_DIR and calls run_flash_command
@app.route('/factory_reset', methods=['POST'])
def factory_reset():
    try:
        data = request.get_json(force=True)
    except:
        data = request.form.to_dict()
    board = (data.get('board') or 'generic').lower()

    # mapping from board -> default filename in DEFAULT_FW_DIR
    default_map = {
        'esp32': 'esp32_default.bin',
        'esp8266': 'esp32_default.bin',
        'arduino': 'arduino_default.hex',
        'attiny': 'attiny_default.hex',
        'stm32': 'stm32_default.bin',
        'nucleo_f446re': 'stm32_default.bin',
        'black_pill': 'stm32_default.bin',
        'msp430': 'generic_default.bin',
        'tiva': 'tiva_default.out',
        'tms320f28377s': 'tms320f28377s_default.out',
        'generic': 'generic_default.bin'
    }

    fname = default_map.get(board, default_map['generic'])
    fpath = os.path.join(DEFAULT_FW_DIR, fname)
    if not os.path.isfile(fpath):
        return jsonify({'error': f'Default firmware not found for board {board}: expected {fpath}'}), 404

    # choose command based on board (similar to /flash)
    port = request.args.get('port') or ''  # optional override
    available_ports = list_serial_ports()
    default_port = available_ports[0] if available_ports else '/dev/ttyUSB0'
    port = port or default_port
    commands = {
        'esp32': f"esptool.py --chip esp32 --port {port} write_flash 0x10000 {fpath}",
        'esp8266': f"esptool.py --port {port} write_flash 0x00000 {fpath}",
        'arduino': f"avrdude -v -p atmega328p -c arduino -P {port} -b115200 -D -U flash:w:{fpath}:i",
        'attiny': f"avrdude -v -p attiny85 -c usbasp -P {port} -U flash:w:{fpath}:i",
        'stm32': f"openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c \"program {fpath} 0x08000000 verify reset exit\"",
        'nucleo_f446re': f"openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c \"program {fpath} 0x08000000 verify reset exit\"",
        'black_pill': f"openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c \"program {fpath} 0x08000000 verify reset exit\"",
        'msp430': f"mspdebug rf2500 'prog {fpath}'",
        'tiva': f"openocd -f board/ti_ek-tm4c123gxl.cfg -c \"program {fpath} verify reset exit\"",
        'tms320f28377s': f"python3 dsp/flash_tool.py {fpath}",
        'generic': f"echo 'No flashing command configured for {board}. Default firmware at {fpath}'"
    }
    cmd = commands.get(board, commands['generic'])
    socketio.start_background_task(run_flash_command, cmd, fname)
    return jsonify({'status': f'Factory reset started for {board}', 'command': cmd})

# ---------- SOP DOWNLOAD ----------
# Serve SOP file(s) from static/sop directory. Example: GET /sop/exp.pdf
@app.route('/sop/<path:filename>')
def serve_sop(filename):
    # security: only allow files inside SOP_DIR
    safe_path = os.path.join(SOP_DIR, filename)
    if not os.path.isfile(safe_path):
        abort(404)
    return send_from_directory(SOP_DIR, filename, as_attachment=True)

# ---------- MOCK GENERATOR (disabled when serial is connected) ----------
def mock_data_generator():
    print("Mock data generator STARTED.")
    try:
        while True:
            # Use generic variable names instead of temp/humid
            sensor1 = 25.0 + random.uniform(-5.0, 5.0)
            sensor2 = 60.0 + random.uniform(-10.0, 10.0)
            sensor3 = 0.5 + random.uniform(-0.2, 0.2)
            sensor4 = 3.3 + random.uniform(-0.5, 0.5)
            payload = {
                'sensor1': round(sensor1, 2),
                'sensor2': round(sensor2, 2),
                'sensor3': round(sensor3, 3),
                'sensor4': round(sensor4, 2)
            }
            socketio.emit('sensor_data', payload)
            eventlet.sleep(0.1)  # faster update rate for smoother chart
    except eventlet.greenlet.GreenletExit:
        print("Mock data generator KILLED.")
    except Exception as e:
        print("Mock data generator error:", e)

# ---------- SERIAL READER ----------
def serial_reader_worker(serial_obj):
    try:
        while not ser_stop.is_set():
            line = serial_obj.readline()
            if not line:
                continue
            try:
                text = line.decode(errors='replace').strip()
            except:
                text = str(line)
            socketio.emit('feedback', text)

            # --- parse serial line into sensor_data for chart ---
            if any(sep in text for sep in [':', '=', '@', '>', '#', '^', '!', '$', '*', '%', '~', '\\', '|', '+', '-', ';', ',']) and any(c.isdigit() for c in text):
                # Flexible parsing similar to client-side
                trimmed = re.sub(r'^\d{1,2}:\d{2}:\d{2}\s*', '', text.strip())  # remove timestamp
                pairGroups = re.split(r'[,;]', trimmed)
                data = {}
                for group in pairGroups:
                    if not group.strip():
                        continue
                    normalized = re.sub(r'[:=>@#>^!$*~\\|+%\s&]+', ' ', group).strip()
                    tokens = re.split(r'\s+', normalized)
                    for i in range(0, len(tokens), 2):
                        if i + 1 < len(tokens):
                            k = tokens[i].strip().lower()
                            rawv = tokens[i + 1].strip()
                            try:
                                num = float(re.sub(r'[^\d\.\-+eE]', '', rawv))
                                if not math.isnan(num):
                                    data[k] = num
                            except:
                                pass
                # Keep original keys as sent by Arduino - no predefined mappings
                if data:
                    socketio.start_background_task(send_sensor_data_to_clients, data)
    except Exception as e:
        socketio.emit('feedback', f'[serial worker stopped] {e}')

# ---------- SOCKET HANDLERS ----------
@socketio.on('connect')
def on_connect():
    from flask import request
    print("[DEBUG] Client connected:", request.sid)
    emit('ports_list', list_serial_ports())
    emit('feedback', 'Server: socket connected')

@socketio.on('list_ports')
def handle_list_ports():
    emit('ports_list', list_serial_ports())

@socketio.on('connect_serial')
def handle_connect_serial(data):
    global ser, ser_stop, data_generator_thread
    port = data.get('port')
    baud = int(data.get('baud', 115200))
    if not port:
        emit('serial_status', {'status': 'error', 'message': 'No port selected'})
        return
    if serial is None:
        emit('serial_status', {'status': 'error', 'message': 'pyserial not available on server'})
        return
    with serial_lock:
        try:
            if ser and ser.is_open:
                ser.close()
            if data_generator_thread:
                data_generator_thread.kill()
                data_generator_thread = None

            ser = serial.Serial(port, baud, timeout=1)
            ser_stop.clear()
            # Use eventlet.spawn instead of threading.Thread
            eventlet.spawn(serial_reader_worker, ser)
            emit('serial_status', {'status': 'connected', 'port': port, 'baud': baud})
        except Exception as e:
            emit('serial_status', {'status': 'error', 'message': str(e)})

@socketio.on('disconnect_serial')
def handle_disconnect_serial():
    global ser, ser_stop, data_generator_thread
    with serial_lock:
        try:
            ser_stop.set()
            if ser and ser.is_open:
                ser.close()
            if data_generator_thread is None:
                data_generator_thread = eventlet.spawn(mock_data_generator)
            emit('serial_status', {'status': 'disconnected'})
        except Exception as e:
            emit('serial_status', {'status': 'error', 'message': str(e)})

@socketio.on('send_command')
def handle_send_command(data):
    global ser
    cmd = data.get('cmd', '')
    out = cmd + ("\n" if not cmd.endswith("\n") else "")
    try:
        with serial_lock:
            if ser and ser.is_open:
                ser.write(out.encode())
                emit('feedback', f'SENT> {cmd}')
            else:
                emit('feedback', f'[no-serial] {cmd}')
    except Exception as e:
        emit('feedback', f'[send error] {e}')

@socketio.on('waveform_config')
def handle_waveform_config(cfg):
    shape = cfg.get('shape'); freq = cfg.get('freq'); amp = cfg.get('amp')
    msg = f'WAVE {shape} FREQ {freq} AMP {amp}'
    emit('feedback', f'[waveform] {msg}')
    with serial_lock:
        try:
            if ser and ser.is_open:
                ser.write((msg + "\n").encode())
        except Exception as e:
            emit('feedback', f'[waveform send error] {e}')

def send_sensor_data_to_clients(data):
    try:
        with app.app_context():
            socketio.emit('sensor_data', data, namespace='/')
            print("[DEBUG] Emitted to clients:", data)
    except Exception as e:
        print("[ERROR] Failed to emit sensor_data:", e)


# ---------- MAIN ----------
if __name__ == '__main__':
    import socket
    def check_port(port, name):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        if result == 0:
            print(f"✓ {name} is running on port {port}")
            return True
        else:
            print(f"✗ {name} is NOT running on port {port}")
            return False

    print("========================================")
    print("Virtual Lab Server Starting...")
    print("========================================")

    audio_running = check_port(9000, "Audio server")
    if not audio_running:
        print("\n⚠️  Audio service not detected!")
        print("   To enable audio, run:")
        print("   sudo systemctl enable audio_stream.service")
        print("   sudo systemctl start audio_stream.service")

    print("\nStarting Flask server on port 5000...")
    print("========================================")

    try:
        socketio.run(app, host='0.0.0.0', port=5000)
    finally:
        print("Main server stopped")