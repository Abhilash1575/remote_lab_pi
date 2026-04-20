#!/usr/bin/env python3
import json
import asyncio
import threading
import base64
import random
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, send, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'virtual-lab-audio-secret'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

audio_sessions = {}
latest_audio = {}

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
    try:
        data = request.json
        sdp = data.get('sdp')
        session_id = data.get('session_id', 'default')
        
        print(f'Received audio offer for session: {session_id}')
        
        if not sdp:
            return jsonify({'error': 'Missing SDP'}), 400
        
        audio_sessions[session_id] = {
            'sdp': sdp,
            'type': 'offer',
            'active': True
        }
        
        answer_sdp = generate_webrtc_answer(sdp)
        
        return jsonify({
            'type': 'answer',
            'sdp': answer_sdp,
            'session_id': session_id
        })
    except Exception as e:
        print(f'Error handling offer: {e}')
        return jsonify({'error': str(e)}), 500

def generate_webrtc_answer(offer_sdp):
    lines = offer_sdp.split('\r\n')
    answer_lines = []
    
    for line in lines:
        if line.startswith('a=mid:'):
            answer_lines.append(line)
        elif line.startswith('a=msid-semantic:'):
            answer_lines.append(line)
        elif line.startswith('a=group:'):
            answer_lines.append(line)
        elif line.startswith('m='):
            answer_lines.append(line.replace('recvonly', 'sendonly'))
        elif line.startswith('a=rtcp-mux'):
            answer_lines.append(line)
        elif line.startswith('a=rtcp-rsize'):
            answer_lines.append(line)
        elif line.startswith('a=ice-options:'):
            answer_lines.append(line)
        elif line.startswith('a=ice-ufrag'):
            answer_lines.append(line.replace(line.split(':')[1], generate_ice_password()))
        elif line.startswith('a=ice-pwd'):
            answer_lines.append(line.replace(line.split(':')[1], generate_ice_password()))
        elif line.startswith('a=candidate'):
            answer_lines.append(line)
        elif line.startswith('a=setup:'):
            answer_lines.append(line.replace('passive', 'active'))
        elif line.startswith('a=rtpmap'):
            answer_lines.append(line)
        elif line.startswith('a=fmtp'):
            answer_lines.append(line)
        elif line.startswith('a=rtcp-fb'):
            answer_lines.append(line)
        elif line.startswith('a=ssrc'):
            answer_lines.append(line)
        elif line == '':
            answer_lines.append(line)
    
    return '\r\n'.join(answer_lines)

def generate_ice_password():
    return ''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=32))

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