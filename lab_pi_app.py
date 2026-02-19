#!/usr/bin/env python3
"""
Virtual Lab - Lab Pi Application (Simplified - No SocketIO)
=========================================================
This runs on each Raspberry Pi (Lab Node) that controls hardware experiments.
It connects to the Master Pi for booking/session management and sends heartbeats.

Usage:
    VLAB_PI_TYPE=lab VLAB_PI_ID=lab-001 VLAB_PI_NAME="LED Blinky Lab" \
        VLAB_PI_MAC=b8:27:eb:xx:xx:xx \
        EXPERIMENT_ID=1 \
        MASTER_URL=http://192.168.1.100:5000 \
        python3 lab_pi_app.py

For testing on same Pi:
    Terminal 1 (Master): python3 app.py
    Terminal 2 (Lab): VLAB_PI_TYPE=lab VLAB_PI_ID=lab-001 python3 lab_pi_app.py
"""

import sys
import os
import time
import threading
import requests
import json
import secrets
import subprocess
from datetime import datetime
from datetime import timezone

# Import psutil for system metrics
try:
    import psutil
except ImportError:
    psutil = None

# Import DFRobot UPS for battery status
try:
    import dfrobot_ups as ups
    UPS_AVAILABLE = True
except ImportError:
    UPS_AVAILABLE = False
    ups = None

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    load_dotenv(env_path)
except ImportError:
    pass

# Import configuration
from config import PI_TYPE, LAB_CONFIG, MASTER_CONFIG, get_config, BASE_DIR, UPLOAD_DIR, DEFAULT_FW_DIR

# Ensure we're running as Lab Pi
if PI_TYPE != 'lab':
    print("ERROR: This application is for Lab Pi only!")
    print("Set VLAB_PI_TYPE=lab or use lab_pi_app.py for Lab Pi")
    sys.exit(1)

from flask import Flask, request, jsonify, render_template

# Import GPIO and Serial modules with fallback
try:
    import lgpio
    RELAY_PIN = LAB_CONFIG['RELAY_PIN']
except Exception as e:
    print(f"lgpio import failed: {e}")
    lgpio = None
    RELAY_PIN = None

try:
    import serial
    from serial.tools import list_ports
except Exception as e:
    serial = None
    list_ports = None

# ============================================================================
# FLASK APP SETUP
# ============================================================================
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = LAB_CONFIG.get('MASTER_API_KEY', LAB_CONFIG['LAB_PI_ID'])
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

# ============================================================================
# LAB PI STATE
# ============================================================================
class LabPiState:
    """Manages the state of this Lab Pi"""
    
    def __init__(self):
        self.lab_pi_id = LAB_CONFIG['LAB_PI_ID']
        self.lab_pi_name = LAB_CONFIG['LAB_PI_NAME']
        self.lab_pi_mac = LAB_CONFIG['LAB_PI_MAC']
        self.experiment_id = LAB_CONFIG['EXPERIMENT_ID']
        self.master_url = LAB_CONFIG['MASTER_URL']
        self.master_api_key = LAB_CONFIG['MASTER_API_KEY']
        
        # Registration status
        self.registered = False
        self.lab_pi_db_id = None
        self.registered_at = None
        
        # Session status
        self.session_active = False
        self.current_session_key = None
        self.session_start_time = None
        self.user_email = None
        
        # Hardware status
        self.hardware_ready = False
        self.relay_state = False
        self.connected_devices = []
        
        # Heartbeat tracking
        self.last_heartbeat_sent = None
        self.last_heartbeat_response = None
        self.heartbeat_failures = 0
        
        # Uptime tracking
        self.start_time = datetime.now(timezone.utc)
    
    def get_uptime(self):
        """Get uptime since Pi started"""
        delta = datetime.now(timezone.utc) - self.start_time
        total_seconds = int(delta.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    def get_headers(self):
        """Get headers for API requests"""
        return {
            'X-Lab-Pi-Id': self.lab_pi_id,
            'X-API-Key': self.master_api_key,
            'Content-Type': 'application/json'
        }


# Global state instance
lab_state = LabPiState()

# ============================================================================
# MASTER PI COMMUNICATOR
# ============================================================================
class MasterPiCommunicator:
    """Handles communication with Master Pi"""
    
    def __init__(self):
        self.base_url = lab_state.master_url
        self.timeout = 10
        
    def _get_uptime(self):
        """Get uptime string"""
        return lab_state.get_uptime()
    
    def _get_system_metrics(self):
        """Get system metrics (CPU, RAM, Temperature, Battery)"""
        metrics = {
            'cpu_usage': None,
            'ram_usage': None,
            'temperature': None,
            'battery_soc': None,
            'battery_voltage': None,
            'battery_ac_status': None,
            'battery_charging': None,
        }
        
        if psutil:
            try:
                # CPU usage
                metrics['cpu_usage'] = psutil.cpu_percent(interval=0.1)
                
                # RAM usage
                mem = psutil.virtual_memory()
                metrics['ram_usage'] = mem.percent
                
                # Temperature (Raspberry Pi)
                try:
                    # Try Raspberry Pi temperature
                    with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                        temp_c = float(f.read()) / 1000.0
                        metrics['temperature'] = temp_c
                except (FileNotFoundError, PermissionError):
                    # Try using psutil.sensors_temperature if available
                    try:
                        temps = psutil.sensors_temperatures()
                        if temps:
                            # Get first temperature reading
                            for name, entries in temps.items():
                                if entries:
                                    metrics['temperature'] = entries[0].current
                                    break
                    except Exception:
                        pass
            except Exception as e:
                print(f"Error getting system metrics: {e}")
        
        # Get battery status from DFRobot UPS
        if UPS_AVAILABLE and ups:
            try:
                metrics['battery_soc'] = ups.read_soc()
                metrics['battery_voltage'] = ups.read_voltage()
                metrics['battery_ac_status'] = ups.ac_status()
                metrics['battery_charging'] = ups.charging_status(metrics['battery_ac_status'], metrics['battery_voltage'])
            except Exception as e:
                print(f"Error getting UPS metrics: {e}")
        
        return metrics
    
    def register(self):
        """Register this Lab Pi with Master Pi"""
        try:
            data = {
                'lab_pi_id': lab_state.lab_pi_id,
                'name': lab_state.lab_pi_name,
                'mac_address': lab_state.lab_pi_mac,
                'ip_address': self._get_ip_address(),
                'hostname': self._get_hostname(),
                'experiment_id': lab_state.experiment_id
            }
            response = requests.post(
                f"{self.base_url}/api/lab-pi/register",
                json=data,
                headers=lab_state.get_headers(),
                timeout=self.timeout
            )
            if response.status_code == 200:
                result = response.json()
                lab_state.lab_pi_db_id = result.get('id')
                lab_state.registered = True
                lab_state.registered_at = datetime.now(timezone.utc)
                print(f"✓ Registered with Master Pi (ID: {lab_state.lab_pi_db_id})")
                return True
            else:
                print(f"✗ Registration failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"✗ Registration error: {e}")
            return False
    
    def heartbeat(self):
        """Send heartbeat to Master Pi"""
        try:
            # Get system metrics
            metrics = self._get_system_metrics()
            
            data = {
                'lab_pi_id': lab_state.lab_pi_id,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'status': 'ONLINE',
                'session_active': lab_state.session_active,
                'session_key': lab_state.current_session_key,
                'relay_state': lab_state.relay_state,
                'hardware_ready': lab_state.hardware_ready,
                'uptime': self._get_uptime(),
                'cpu_usage': metrics['cpu_usage'],
                'ram_usage': metrics['ram_usage'],
                'temperature': metrics['temperature'],
                'battery_soc': metrics['battery_soc'],
                'battery_voltage': metrics['battery_voltage'],
                'battery_ac_status': metrics['battery_ac_status'],
                'battery_charging': metrics['battery_charging'],
            }
            response = requests.post(
                f"{self.base_url}/api/lab-pi/heartbeat",
                json=data,
                headers=lab_state.get_headers(),
                timeout=self.timeout
            )
            if response.status_code == 200:
                lab_state.last_heartbeat_sent = datetime.now(timezone.utc)
                result = response.json()
                lab_state.last_heartbeat_response = result
                lab_state.heartbeat_failures = 0
                
                # Check if there's a new session assigned
                if result.get('new_session'):
                    self.handle_new_session(result['session'])
                return True
            else:
                print(f"✗ Heartbeat failed: {response.status_code}")
                lab_state.heartbeat_failures += 1
                return False
        except Exception as e:
            print(f"✗ Heartbeat error: {e}")
            lab_state.heartbeat_failures += 1
            return False
    
    def handle_new_session(self, session_data):
        """Handle new session assigned by Master Pi"""
        print(f"📡 New session received: {session_data}")
        lab_state.session_active = True
        lab_state.current_session_key = session_data.get('session_key')
        lab_state.session_start_time = datetime.now(timezone.utc)
        lab_state.user_email = session_data.get('user_email')
        
        # Power on hardware
        self.power_on_hardware()
    
    def end_session(self, session_key):
        """Notify Master Pi that session has ended"""
        try:
            data = {
                'lab_pi_id': lab_state.lab_pi_id,
                'session_key': session_key
            }
            response = requests.post(
                f"{self.base_url}/api/lab-pi/session-end",
                json=data,
                headers=lab_state.get_headers(),
                timeout=self.timeout
            )
            return response.status_code == 200
        except Exception as e:
            print(f"✗ End session error: {e}")
            return False
    
    def power_on_hardware(self):
        """Power on the hardware for experiment"""
        print("🔌 Powering on hardware...")
        if lgpio and RELAY_PIN:
            try:
                lgpio.gpio_write(RELAY_PIN, 1)
                lab_state.relay_state = True
                print("✓ Hardware powered on")
            except Exception as e:
                print(f"✗ Failed to power on hardware: {e}")
    
    def power_off_hardware(self):
        """Power off the hardware after session ends"""
        print("🔌 Powering off hardware...")
        if lgpio and RELAY_PIN:
            try:
                lgpio.gpio_write(RELAY_PIN, 0)
                lab_state.relay_state = False
                print("✓ Hardware powered off")
            except Exception as e:
                print(f"✗ Failed to power off hardware: {e}")
    
    def _get_ip_address(self):
        """Get IP address of this Pi"""
        try:
            s = subprocess.socket(subprocess.AF_INET, subprocess.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return '127.0.0.1'
    
    def _get_hostname(self):
        """Get hostname of this Pi"""
        try:
            return subprocess.check_output('hostname', text=True).strip()
        except:
            return 'unknown'


# Create communicator instance
master_comm = MasterPiCommunicator()

# ============================================================================
# BACKGROUND THREADS
# ============================================================================
def heartbeat_thread():
    """Background thread to send heartbeats"""
    while True:
        try:
            if lab_state.registered:
                success = master_comm.heartbeat()
                if not success:
                    print(f"⚠ Heartbeat failed ({lab_state.heartbeat_failures} failures)")
                    
                    # If too many failures, try to re-register
                    if lab_state.heartbeat_failures >= 5:
                        print("🔄 Too many heartbeat failures, re-registering...")
                        lab_state.registered = False
                        master_comm.register()
            else:
                # Try to register
                print("📡 Not registered, attempting registration...")
                master_comm.register()
        except Exception as e:
            print(f"⚠ Heartbeat thread error: {e}")
        
        # Wait for next heartbeat interval
        time.sleep(30)


def session_monitor_thread():
    """Background thread to monitor session timeout"""
    while True:
        try:
            if lab_state.session_active and lab_state.session_start_time:
                # Check if session has exceeded max duration (default 60 minutes)
                max_duration = 60 * 60  # 60 minutes
                elapsed = (datetime.now(timezone.utc) - lab_state.session_start_time).total_seconds()
                
                if elapsed > max_duration:
                    print("⏰ Session timeout, ending session...")
                    end_session()
        except Exception as e:
            print(f"⚠ Session monitor error: {e}")
        
        time.sleep(60)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
def end_session():
    """End current session and cleanup"""
    if lab_state.current_session_key:
        master_comm.end_session(lab_state.current_session_key)
        master_comm.power_off_hardware()
        
        lab_state.session_active = False
        lab_state.current_session_key = None
        lab_state.session_start_time = None
        lab_state.user_email = None


def generate_session_key():
    """Generate a unique session key"""
    return secrets.token_urlsafe(16)


# ============================================================================
# FLASK ROUTES
# ============================================================================
@app.route('/')
def index():
    """Lab Pi status page"""
    return render_template('lab_pi/index.html', 
                         lab_pi_name=lab_state.lab_pi_name,
                         lab_pi_id=lab_state.lab_pi_id,
                         registered=lab_state.registered,
                         session_active=lab_state.session_active,
                         session_key=lab_state.current_session_key,
                         user_email=lab_state.user_email,
                         uptime=lab_state.get_uptime(),
                         relay_state=lab_state.relay_state)


@app.route('/camera')
def camera():
    """Camera stream page"""
    return render_template('lab_pi/camera.html')


@app.route('/status')
def status():
    """JSON status endpoint"""
    return jsonify({
        'lab_pi_id': lab_state.lab_pi_id,
        'name': lab_state.lab_pi_name,
        'registered': lab_state.registered,
        'session_active': lab_state.session_active,
        'session_key': lab_state.current_session_key,
        'user_email': lab_state.user_email,
        'uptime': lab_state.get_uptime(),
        'relay_state': lab_state.relay_state,
        'hardware_ready': lab_state.hardware_ready,
        'registered_at': lab_state.registered_at.isoformat() if lab_state.registered_at else None,
        'last_heartbeat': lab_state.last_heartbeat_sent.isoformat() if lab_state.last_heartbeat_sent else None
    })


@app.route('/api/status')
def api_status():
    """JSON status endpoint for Master Pi API"""
    return jsonify({
        'lab_pi_id': lab_state.lab_pi_id,
        'name': lab_state.lab_pi_name,
        'registered': lab_state.registered,
        'session_active': lab_state.session_active,
        'session_key': lab_state.current_session_key,
        'user_email': lab_state.user_email,
        'uptime': lab_state.get_uptime(),
        'relay_state': lab_state.relay_state,
        'hardware_ready': lab_state.hardware_ready,
        'registered_at': lab_state.registered_at.isoformat() if lab_state.registered_at else None,
        'last_heartbeat': lab_state.last_heartbeat_sent.isoformat() if lab_state.last_heartbeat_sent else None
    })


@app.route('/api/relay/on', methods=['POST'])
def relay_on():
    """Turn relay on"""
    if not lab_state.session_active:
        return jsonify({'error': 'No active session'}), 403
    
    if lgpio and RELAY_PIN:
        try:
            lgpio.gpio_write(RELAY_PIN, 1)
            lab_state.relay_state = True
            return jsonify({'success': True, 'state': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    return jsonify({'success': True, 'state': True, 'simulated': True})


@app.route('/api/relay/off', methods=['POST'])
def relay_off():
    """Turn relay off"""
    if not lab_state.session_active:
        return jsonify({'error': 'No active session'}), 403
    
    if lgpio and RELAY_PIN:
        try:
            lgpio.gpio_write(RELAY_PIN, 0)
            lab_state.relay_state = False
            return jsonify({'success': True, 'state': False})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    return jsonify({'success': True, 'state': False, 'simulated': True})


@app.route('/api/relay/status', methods=['GET'])
def relay_status():
    """Get relay status"""
    return jsonify({'state': lab_state.relay_state})


@app.route('/api/session/end', methods=['POST'])
def api_end_session():
    """End session via API (called from Master Pi)"""
    if not lab_state.session_active:
        return jsonify({'error': 'No active session'}), 400
    
    end_session()
    return jsonify({'success': True})


# ============================================================================
# MASTER PI API (Called by Master Pi)
# ============================================================================
@app.route('/api/lab-pi/session-start', methods=['POST'])
def lab_pi_session_start():
    """Called by Master Pi to start a session on this Lab Pi"""
    # Verify the request is from Master Pi
    lab_pi_id = request.headers.get('X-Lab-Pi-Id')
    if lab_pi_id != lab_state.lab_pi_id:
        return jsonify({'error': 'Invalid Lab Pi ID'}), 401
    
    data = request.get_json()
    session_key = data.get('session_key')
    booking_id = data.get('booking_id')
    user_email = data.get('user_email')
    
    print(f"🎬 Starting session: {session_key} for {user_email}")
    
    lab_state.session_active = True
    lab_state.current_session_key = session_key
    lab_state.session_start_time = datetime.now(timezone.utc)
    lab_state.user_email = user_email
    
    # Power on hardware
    master_comm.power_on_hardware()
    
    return jsonify({
        'success': True,
        'session_key': session_key,
        'lab_pi_id': lab_state.lab_pi_id
    })


@app.route('/api/lab-pi/session-end', methods=['POST'])
def lab_pi_session_end():
    """Called by Master Pi to end a session on this Lab Pi"""
    data = request.get_json()
    session_key = data.get('session_key')
    
    print(f"🛑 Ending session: {session_key}")
    
    lab_state.session_active = False
    lab_state.current_session_key = None
    lab_state.session_start_time = None
    lab_state.user_email = None
    
    # Power off hardware
    master_comm.power_off_hardware()
    
    return jsonify({
        'success': True,
        'lab_pi_id': lab_state.lab_pi_id
    })


@app.route('/api/command', methods=['POST'])
def api_command():
    """Handle commands from Master Pi (restart, reboot, etc.)"""
    import subprocess
    
    data = request.get_json()
    command = data.get('command')
    
    print(f"📩 Received command: {command}")
    
    if command == 'restart':
        # Restart the Lab Pi Flask server
        def delayed_restart():
            import time
            time.sleep(1)
            # Use systemd to restart the service
            subprocess.run(['sudo', 'systemctl', 'restart', 'vlab-lab-pi.service'], check=False)
        
        threading.Thread(target=delayed_restart, daemon=True).start()
        
        return jsonify({
            'success': True,
            'message': 'Restarting Lab Pi server...'
        })
    
    elif command == 'reboot':
        # Reboot the Raspberry Pi hardware
        def delayed_reboot():
            import time
            time.sleep(1)
            subprocess.run(['sudo', 'reboot'], check=False)
        
        threading.Thread(target=delayed_reboot, daemon=True).start()
        
        return jsonify({
            'success': True,
            'message': 'Rebooting Lab Pi hardware...'
        })
    
    else:
        return jsonify({'error': 'Unknown command'}), 400


# ============================================================================
# MAIN
# ============================================================================
if __name__ == '__main__':
    print("=" * 60)
    print("Virtual Lab - Lab Pi")
    print("=" * 60)
    print(f"Lab Pi ID: {lab_state.lab_pi_id}")
    print(f"Lab Pi Name: {lab_state.lab_pi_name}")
    print(f"Master URL: {lab_state.master_url}")
    print(f"Experiment ID: {lab_state.experiment_id}")
    print("=" * 60)
    
    # Start background threads
    heartbeat_thread_handle = threading.Thread(target=heartbeat_thread, daemon=True)
    heartbeat_thread_handle.start()
    
    session_monitor_handle = threading.Thread(target=session_monitor_thread, daemon=True)
    session_monitor_handle.start()
    
    # Initial registration
    print("\n📡 Attempting initial registration...")
    master_comm.register()
    
    # Run Flask server
    host = LAB_CONFIG.get('HOST', '0.0.0.0')
    port = LAB_CONFIG.get('PORT', 5001)
    print(f"\n🚀 Starting Lab Pi server on {host}:{port}")
    
    app.run(host=host, port=port, debug=False, threaded=True)
