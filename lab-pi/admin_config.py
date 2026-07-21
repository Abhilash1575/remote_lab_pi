"""Admin auth + per-Pi UI control configuration.

Local, LAN-only admin gate: a single shared password (hashed) protects
/admin/settings, which toggles which student-facing controls are enabled
and which view loads by default. State persists in ui_config.json.
"""
import os
import json
import time
import uuid
from functools import wraps

from flask import session, redirect, url_for, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_CONFIG_PATH = os.path.join(BASE_DIR, 'ui_config.json')
ADMIN_PW_HASH_PATH = os.path.join(BASE_DIR, 'admin_password.hash')

# Extensible registry: (key, label). Missing keys in an on-disk config
# default to enabled, so adding a new entry here never breaks old files.
CONTROL_KEYS = [
    ('flash_firmware', 'Flash Firmware'),
    ('board_select', 'Board Select Dropdown'),
    ('serial_connect', 'Serial Monitor Connect'),
    ('factory_reset', 'Factory Reset'),
    ('serial_plotter', 'Serial Plotter View'),
    ('power_supply', 'Power Supply ON/OFF'),
    ('serial_monitor_section', 'Serial Monitor & Connect (whole section)'),
    ('oscilloscope', 'Oscilloscope View'),
    ('student_controls_addition', "Students Can Add Their Own Dynamic Controls"),
]

DEFAULT_UI_CONFIG = {
    'version': 1,
    'controls': {key: True for key, _ in CONTROL_KEYS},
    'defaults': {
        'main_view': 'plotter',
        'dynamic_controls_visible': True,
        'serial_monitor_auto_connect': False,
        'serial_monitor_allow_disconnect': True,
    },
    'required_controls': [],
    'experiment_name': 'Remote Lab DESE',
    'updated_at': None,
}

_cache = None


def load_ui_config(force_reload=False):
    global _cache
    if _cache is not None and not force_reload:
        return _cache
    cfg = json.loads(json.dumps(DEFAULT_UI_CONFIG))
    if os.path.isfile(UI_CONFIG_PATH):
        try:
            with open(UI_CONFIG_PATH) as f:
                on_disk = json.load(f)
            cfg['controls'].update(on_disk.get('controls', {}))
            cfg['defaults'].update(on_disk.get('defaults', {}))
            if 'required_controls' in on_disk:
                cfg['required_controls'] = on_disk['required_controls']
            if on_disk.get('experiment_name'):
                cfg['experiment_name'] = on_disk['experiment_name']
            cfg['updated_at'] = on_disk.get('updated_at')
        except Exception as e:
            print(f"[AdminConfig] Failed to load ui_config.json, using defaults: {e}")
    _cache = cfg
    return cfg


def get_effective_ui_config():
    """load_ui_config() plus reconciliation so settings never contradict
    each other (e.g. defaulting to a view that's currently disabled)."""
    cfg = json.loads(json.dumps(load_ui_config()))
    controls = cfg['controls']
    main_view = cfg['defaults'].get('main_view')
    if main_view == 'plotter' and not controls.get('serial_plotter', True):
        cfg['defaults']['main_view'] = 'oscilloscope' if controls.get('oscilloscope', True) else main_view
    elif main_view == 'oscilloscope' and not controls.get('oscilloscope', True):
        cfg['defaults']['main_view'] = 'plotter' if controls.get('serial_plotter', True) else main_view
    # serial_monitor_section only hides the card in the UI; serial_connect is the sole
    # functional gate, so auto-connect still runs even when the section is hidden.
    if not controls.get('serial_connect', True):
        cfg['defaults']['serial_monitor_auto_connect'] = False
    return cfg


def _persist(cfg):
    cfg['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    with open(UI_CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)
    global _cache
    _cache = cfg
    return cfg


def save_ui_config(new_controls, new_defaults, experiment_name=None):
    cfg = load_ui_config()
    cfg['controls'].update(new_controls)
    cfg['defaults'].update(new_defaults)
    if experiment_name:
        cfg['experiment_name'] = experiment_name
    return _persist(cfg)


def add_required_control(control):
    cfg = load_ui_config()
    control = dict(control)
    control['id'] = uuid.uuid4().hex[:10]
    cfg.setdefault('required_controls', []).append(control)
    _persist(cfg)
    return control


def delete_required_control(control_id):
    cfg = load_ui_config()
    cfg['required_controls'] = [c for c in cfg.get('required_controls', []) if c.get('id') != control_id]
    return _persist(cfg)


def is_control_enabled(key):
    return load_ui_config().get('controls', {}).get(key, True)


# ---------- password ----------

def _stored_hash():
    env_hash = os.environ.get('ADMIN_PASSWORD_HASH')
    if env_hash:
        return env_hash
    if os.path.isfile(ADMIN_PW_HASH_PATH):
        with open(ADMIN_PW_HASH_PATH) as f:
            return f.read().strip()
    return None


def has_admin_password_configured():
    return _stored_hash() is not None


def password_locked_by_env():
    return bool(os.environ.get('ADMIN_PASSWORD_HASH'))


def set_admin_password(new_password):
    h = generate_password_hash(new_password)
    with open(ADMIN_PW_HASH_PATH, 'w') as f:
        f.write(h)
    os.chmod(ADMIN_PW_HASH_PATH, 0o600)


def verify_admin_password(password):
    stored = _stored_hash()
    return bool(stored) and check_password_hash(stored, password)


# ---------- auth guard ----------

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('is_admin'):
            if request.is_json:
                return jsonify({'error': 'admin auth required'}), 401
            return redirect(url_for('admin_login', next=request.path))
        return view(*args, **kwargs)
    return wrapped
