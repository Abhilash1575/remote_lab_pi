#!/usr/bin/env python3
"""
Audio Capture Module for Lab Pi
Captures audio from USB microphone and streams to Master Pi
"""

import threading
import queue
import time
import json
import requests
import io
import base64

# Audio configuration
CHUNK_SIZE = 1024  # Samples per buffer
SAMPLE_RATE = 16000  # Hz (lower for efficiency)
CHANNELS = 1  # Mono
FORMAT = 'int16'

# PyAudio import with fallback
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    pyaudio = None


class AudioCapture:
    """Captures audio from microphone and streams to Master Pi"""
    
    def __init__(self, master_url, lab_pi_id, sample_rate=SAMPLE_RATE, 
                 chunk_size=CHUNK_SIZE, channels=CHANNELS):
        self.master_url = master_url.rstrip('/')
        self.lab_pi_id = lab_pi_id
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels
        
        self.stream = None
        self.audio = None
        self.streaming = False
        self.stream_thread = None
        self.audio_queue = queue.Queue(maxsize=10)
        
    def _get_default_input_device(self):
        """Find the default input device"""
        if not PYAUDIO_AVAILABLE or not self.audio:
            return None
        
        try:
            for i in range(self.audio.get_device_count()):
                device_info = self.audio.get_device_info_by_index(i)
                if device_info['maxInputChannels'] > 0:
                    return i
        except Exception:
            pass
        return None
    
    def initialize(self):
        """Initialize PyAudio and find microphone"""
        if not PYAUDIO_AVAILABLE:
            print("PyAudio not available - audio capture disabled")
            return False
        
        try:
            self.audio = pyaudio.PyAudio()
            device_index = self._get_default_input_device()
            
            if device_index is None:
                print("No input device found - audio capture disabled")
                return False
            
            # Open audio stream
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=self.chunk_size
            )
            
            print(f"Audio capture initialized on device {device_index}")
            return True
            
        except Exception as e:
            print(f"Failed to initialize audio: {e}")
            return False
    
    def start(self):
        """Start audio capture in background thread"""
        if self.streaming:
            return True
            
        if not self.initialize():
            return False
            
        self.streaming = True
        self.stream_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.stream_thread.start()
        
        # Start streaming thread to send to Master Pi
        self.send_thread = threading.Thread(target=self._send_loop, daemon=True)
        self.send_thread.start()
        
        print("Audio capture started")
        return True
    
    def stop(self):
        """Stop audio capture"""
        self.streaming = False
        
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
            
        if self.audio:
            try:
                self.audio.terminate()
            except Exception:
                pass
            self.audio = None
            
        print("Audio capture stopped")
    
    def _capture_loop(self):
        """Background thread: capture audio chunks"""
        while self.streaming:
            try:
                if self.stream and self.stream.is_active():
                    data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                    # Convert to base64 for transmission
                    audio_b64 = base64.b64encode(data).decode('utf-8')
                    
                    try:
                        self.audio_queue.put(audio_b64, timeout=0.5)
                    except queue.Full:
                        pass  # Skip frame if queue full
                else:
                    time.sleep(0.01)
            except Exception as e:
                print(f"Audio capture error: {e}")
                time.sleep(0.1)
    
    def _send_loop(self):
        """Background thread: send audio to Master Pi"""
        while self.streaming:
            try:
                if not self.audio_queue.empty():
                    audio_b64 = self.audio_queue.get()
                    
                    # Send to Master Pi
                    try:
                        response = requests.post(
                            f"{self.master_url}/api/audio/stream",
                            json={
                                'lab_pi_id': self.lab_pi_id,
                                'audio': audio_b64,
                                'sample_rate': self.sample_rate,
                                'channels': self.channels
                            },
                            timeout=1
                        )
                    except requests.RequestException:
                        pass  # Ignore network errors
                        
                else:
                    time.sleep(0.01)
            except Exception as e:
                print(f"Audio send error: {e}")
                time.sleep(0.1)
    
    def is_active(self):
        """Check if audio streaming is active"""
        return self.streaming


# Global audio capture instance
_audio_capture = None


def get_audio_capture():
    """Get the global audio capture instance"""
    return _audio_capture


def init_audio_capture(master_url, lab_pi_id):
    """Initialize and return audio capture instance"""
    global _audio_capture
    
    if not PYAUDIO_AVAILABLE:
        print("Warning: PyAudio not installed. Install with: pip install pyaudio")
        return None
    
    _audio_capture = AudioCapture(master_url, lab_pi_id)
    return _audio_capture


def start_audio_stream():
    """Start audio streaming"""
    if _audio_capture:
        return _audio_capture.start()
    return False


def stop_audio_stream():
    """Stop audio streaming"""
    if _audio_capture:
        _audio_capture.stop()


def is_audio_streaming():
    """Check if audio is streaming"""
    if _audio_capture:
        return _audio_capture.is_active()
    return False
