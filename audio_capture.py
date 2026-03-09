#!/usr/bin/env python3
"""
Audio Capture Module for Lab Pi
Captures audio from USB microphone and streams to Master Pi
Supports PyAudio with ALSA fallback for better compatibility
"""

import threading
import queue
import time
import json
import requests
import io
import base64
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Audio configuration
CHUNK_SIZE = 1024  # Samples per buffer
SAMPLE_RATE = 16000  # Hz (lower for efficiency)
CHANNELS = 1  # Mono
FORMAT = 'int16'

# Try multiple audio backends
audio_backend = None
pyaudio = None
alsa_available = False

# Try PyAudio first
try:
    import pyaudio
    audio_backend = 'pyaudio'
    logger.info("Using PyAudio for audio capture")
except ImportError:
    logger.warning("PyAudio not available, trying ALSA...")
    pyaudio = None

# Fallback to ALSA if PyAudio not available
if pyaudio is None:
    try:
        import alsaaudio
        alsa_available = True
        audio_backend = 'alsa'
        logger.info("Using ALSA for audio capture")
    except ImportError:
        logger.warning("ALSA not available either")
        alsa_available = False


class ALSA AudioCapture:
    """Captures audio using ALSA (Advanced Linux Sound Architecture)"""
    
    def __init__(self, master_url, lab_pi_id, sample_rate=SAMPLE_RATE, 
                 chunk_size=CHUNK_SIZE, channels=CHANNELS):
        self.master_url = master_url.rstrip('/')
        self.lab_pi_id = lab_pi_id
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels
        
        self.stream = None
        self.streaming = False
        self.stream_thread = None
        self.audio_queue = queue.Queue(maxsize=10)
        
    def initialize(self):
        """Initialize ALSA audio capture"""
        if not alsa_available:
            logger.error("ALSA not available")
            return False
        
        try:
            # Open PCM device for recording
            self.stream = alsaaudio.PCM(
                alsaaudio.PCM_CAPTURE,
                alsaaudio.PCM_NORMAL,
                device='default'
            )
            # Set attributes
            self.stream.setchannels(self.channels)
            self.stream.setformat(alsaaudio.PCM_FORMAT_S16_LE)
            self.stream.setperiodsize(self.chunk_size)
            
            logger.info("ALSA audio capture initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize ALSA audio: {e}")
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
        
        logger.info("ALSA audio capture started")
        return True
    
    def stop(self):
        """Stop audio capture"""
        self.streaming = False
        
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
            
        logger.info("ALSA audio capture stopped")
    
    def _capture_loop(self):
        """Background thread: capture audio chunks"""
        while self.streaming:
            try:
                if self.stream:
                    length, data = self.stream.read()
                    if length > 0:
                        # Convert to base64 for transmission
                        audio_b64 = base64.b64encode(data).decode('utf-8')
                        
                        try:
                            self.audio_queue.put(audio_b64, timeout=0.5)
                        except queue.Full:
                            pass  # Skip frame if queue full
            except Exception as e:
                logger.error(f"ALSA audio capture error: {e}")
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
                logger.error(f"Audio send error: {e}")
                time.sleep(0.1)
    
    def is_active(self):
        """Check if audio streaming is active"""
        return self.streaming


class PyAudioCapture:
    """Captures audio using PyAudio"""
    
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
        if not pyaudio or not self.audio:
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
        if not pyaudio:
            logger.error("PyAudio not available")
            return False
        
        try:
            self.audio = pyaudio.PyAudio()
            device_index = self._get_default_input_device()
            
            if device_index is None:
                logger.error("No input device found")
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
            
            logger.info(f"PyAudio capture initialized on device {device_index}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize PyAudio: {e}")
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
        
        logger.info("PyAudio capture started")
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
            
        logger.info("PyAudio capture stopped")
    
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
                logger.error(f"Audio capture error: {e}")
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
                logger.error(f"Audio send error: {e}")
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
    """Initialize and return audio capture instance based on available backend"""
    global _audio_capture, audio_backend
    
    if audio_backend == 'pyaudio':
        logger.info("Initializing PyAudio capture...")
        _audio_capture = PyAudioCapture(master_url, lab_pi_id)
        return _audio_capture
    elif audio_backend == 'alsa':
        logger.info("Initializing ALSA capture...")
        _audio_capture = ALSACapture(master_url, lab_pi_id)
        return _audio_capture
    else:
        logger.error("No audio backend available. Install PyAudio or python-alsaaudio")
        return None


def start_audio_stream():
    """Start audio streaming"""
    if _audio_capture:
        return _audio_capture.start()
    logger.error("Audio capture not initialized")
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


def get_audio_backend():
    """Get the current audio backend"""
    return audio_backend


# Test function to check audio availability
def test_audio():
    """Test audio input availability"""
    logger.info(f"Testing audio with backend: {audio_backend}")
    
    if audio_backend == 'pyaudio':
        try:
            audio = pyaudio.PyAudio()
            device_count = audio.get_device_count()
            logger.info(f"Found {device_count} audio devices")
            
            for i in range(device_count):
                info = audio.get_device_info_by_index(i)
                if info['maxInputChannels'] > 0:
                    logger.info(f"  Input device {i}: {info['name']}")
            
            audio.terminate()
            return True
        except Exception as e:
            logger.error(f"Audio test failed: {e}")
            return False
    
    elif audio_backend == 'alsa':
        try:
            import alsaaudio
            devices = alsaaudio.pcms(alsaaudio.PCM_CAPTURE)
            logger.info(f"ALSA capture devices: {devices}")
            return len(devices) > 0
        except Exception as e:
            logger.error(f"ALSA test failed: {e}")
            return False
    
    logger.error("No audio backend available")
    return False


if __name__ == '__main__':
    # Test audio when run directly
    test_audio()
