#!/usr/bin/env python3
import json
import os
import re
import asyncio
import threading
import subprocess
import base64
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, send, emit
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

app = Flask(__name__)
app.config['SECRET_KEY'] = 'virtual-lab-audio-secret'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

audio_sessions = {}
latest_audio = {}

# ---------- WebRTC (real aiortc peer connections) ----------

def _ensure_alsa_config_workaround():
    """The aarch64 PyAV wheel bundles a static alsa-lib compiled with a
    hardcoded confdir from its own build environment (/tmp/vendor/share/alsa).
    Without a real alsa.conf tree at that exact path, av.open(..., format='alsa')
    fails for EVERY device string (including 'default') with "Unknown PCM ...".
    Symlinking the real system config into that path is safe and idempotent --
    cheap enough to redo on every process start rather than depend on
    machine-specific setup."""
    try:
        vendor_dir = '/tmp/vendor/share'
        os.makedirs(vendor_dir, exist_ok=True)
        link = os.path.join(vendor_dir, 'alsa')
        if not os.path.islink(link) or os.readlink(link) != '/usr/share/alsa':
            if os.path.lexists(link):
                os.remove(link)
            os.symlink('/usr/share/alsa', link)
    except OSError as e:
        print(f'[WebRTC] Could not set up ALSA config workaround: {e}')

def _detect_capture_device():
    """Find the Lab Pi's microphone. 'default' can't be relied on -- on
    this hardware (and likely others) the system default PCM is asymmetric
    and has no capture side at all (the onboard output has no mic), while
    the real microphone is a USB device enumerated on a different card.
    Auto-detect the first capture-capable card via `arecord -l`; override
    with AUDIO_INPUT_DEVICE in .env for a Pi with multiple capture devices
    where auto-detection picks the wrong one."""
    override = os.environ.get('AUDIO_INPUT_DEVICE')
    if override:
        return override
    try:
        output = subprocess.run(
            ['arecord', '-l'], capture_output=True, text=True, timeout=5
        ).stdout
        match = re.search(r'^card (\d+):.*device (\d+):', output, re.MULTILINE)
        if match:
            return f'plughw:{match.group(1)},{match.group(2)}'
    except (OSError, subprocess.SubprocessError) as e:
        print(f'[WebRTC] ALSA capture device auto-detect failed: {e}')
    return 'default'

_ensure_alsa_config_workaround()
MIC_DEVICE = _detect_capture_device()
print(f'[WebRTC] Using audio capture device: {MIC_DEVICE}')

# aiortc needs a persistently-running asyncio event loop (media keeps
# flowing after the HTTP response returns), but Flask's routes are sync.
# Run one event loop for the process lifetime in a background thread and
# hand coroutines to it from the Flask request thread.
_webrtc_loop = asyncio.new_event_loop()
threading.Thread(target=_webrtc_loop.run_forever, daemon=True).start()

def run_on_webrtc_loop(coro, timeout=15):
    return asyncio.run_coroutine_threadsafe(coro, _webrtc_loop).result(timeout=timeout)

# One active RTCPeerConnection per session_id. A reconnect (page refresh,
# retry) for the same session closes the previous connection first so we
# don't leak PeerConnections or hold the mic open from an abandoned one.
peer_connections = {}

async def _close_pc(session_id):
    pc = peer_connections.pop(session_id, None)
    if pc is not None:
        await pc.close()

async def _handle_offer_async(session_id, sdp, type_):
    await _close_pc(session_id)

    pc = RTCPeerConnection()
    peer_connections[session_id] = pc

    @pc.on('connectionstatechange')
    async def on_connectionstatechange():
        print(f'[WebRTC] session {session_id} connection state: {pc.connectionState}')
        if pc.connectionState in ('failed', 'closed'):
            await _close_pc(session_id)

    player = MediaPlayer(MIC_DEVICE, format='alsa')
    if player.audio is None:
        await pc.close()
        peer_connections.pop(session_id, None)
        raise RuntimeError(f'No audio track available from input device "{MIC_DEVICE}"')
    pc.addTrack(player.audio)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=type_))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return pc.localDescription.sdp, pc.localDescription.type

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'audio'})

@app.route('/api/audio/stream', methods=['POST'])
def receive_audio():
    try:
        data = request.json
        lab_pi_id = data.get('lab_pi_id')
        audio_b64 = data.get('audio')
        sample_rate = data.get('sample_rate', 16000)
        channels = data.get('channels', 1)
        
        if not lab_pi_id or not audio_b64:
            return jsonify({'error': 'Missing lab_pi_id or audio'}), 400
        
        latest_audio[lab_pi_id] = {
            'audio': audio_b64,
            'sample_rate': sample_rate,
            'channels': channels
        }
        
        socketio.emit('audio_data', {
            'lab_pi_id': lab_pi_id,
            'audio': audio_b64,
            'sample_rate': sample_rate,
            'channels': channels
        }, namespace='/audio')
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f'Error receiving audio: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/offer', methods=['POST'])
def handle_offer():
    data = request.json
    sdp = data.get('sdp')
    type_ = data.get('type', 'offer')
    session_id = data.get('session_id', 'default')

    print(f'Received audio offer for session: {session_id}')

    if not sdp:
        return jsonify({'error': 'Missing SDP'}), 400

    try:
        answer_sdp, answer_type = run_on_webrtc_loop(_handle_offer_async(session_id, sdp, type_))
    except Exception as e:
        print(f'Error handling offer: {e}')
        return jsonify({'error': str(e)}), 500

    return jsonify({
        'type': answer_type,
        'sdp': answer_sdp,
        'session_id': session_id
    })

@app.route('/status')
def status():
    return jsonify({
        'status': 'running',
        'sessions': len(audio_sessions),
        'active': len([s for s in audio_sessions.values() if s.get('active', False)])
    })

@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected: {request.sid}')
    for session_id, session in list(audio_sessions.items()):
        if session.get('sid') == request.sid:
            del audio_sessions[session_id]
            break

@socketio.on('audio_start')
def handle_audio_start(data):
    session_id = data.get('session_id', 'default')
    lab_pi_id = data.get('lab_pi_id')
    
    audio_sessions[session_id] = {
        'sid': request.sid,
        'active': True,
        'lab_pi_id': lab_pi_id
    }
    
    if lab_pi_id and lab_pi_id in latest_audio:
        emit('audio_data', latest_audio[lab_pi_id], namespace='/audio')
    
    print(f'Audio session started: {session_id} for Lab Pi: {lab_pi_id}')
    emit('audio_started', {'session_id': session_id})

@socketio.on('audio_stop')
def handle_audio_stop(data):
    session_id = data.get('session_id', 'default')
    if session_id in audio_sessions:
        del audio_sessions[session_id]
        print(f'Audio session stopped: {session_id}')
    emit('audio_stopped', {'session_id': session_id})

def run_server(port=9000):
    print(f'Starting Audio Streaming Server on port {port}...')
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)

if __name__ == '__main__':
    run_server()