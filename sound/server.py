import json
import asyncio
import fractions
import subprocess
import os

# Set ALSA config path to system location (not venv)
os.environ['ALSA_CONFIG_PATH'] = '/usr/share/alsa/alsa.conf'

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from aiortc.contrib.media import MediaPlayer
from av import AudioFrame
import time

# Get the directory where this script is located
AUDIO_DIR = os.path.dirname(os.path.abspath(__file__))

def get_available_audio_devices():
    """Get list of available ALSA audio devices"""
    devices = []
    try:
        # Try to get list of audio devices using aplay
        result = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'card' in line and 'device' in line:
                    devices.append(line.strip())
    except Exception as e:
        print(f"Error getting audio devices: {e}")

    try:
        # Also try arecord -l
        result = subprocess.run(["arecord", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'card' in line and 'device' in line:
                    devices.append(f"MIC: {line.strip()}")
    except Exception as e:
        print(f"Error getting recording devices: {e}")

    return devices

def get_capture_cards():
    """Get list of available capture card numbers"""
    cards = []
    try:
        result = subprocess.run(["arecord", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('card '):
                    # Extract card number: "card 1: Audio [Lenovo FHD Webcam Audio]"
                    parts = line.split(':')
                    if len(parts) > 0:
                        card_num = parts[0].replace('card ', '').strip()
                        try:
                            cards.append(int(card_num))
                        except ValueError:
                            pass
    except Exception as e:
        print(f"Error getting capture cards: {e}")
    return cards

class SilenceAudioTrack(MediaStreamTrack):
    kind = "audio"

    async def recv(self):
        # Generate 20ms of silence at 48kHz
        samples = 960
        frame = AudioFrame(format="s16", layout="mono", samples=samples)
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))

        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.sample_rate = 48000
        frame.time_base = time_base
        return frame

class AudioRestartManager:
    def __init__(self):
        self.restart_count = 0
        self.max_restarts = 3
        self.restart_delay = 2.0

    async def should_restart(self):
        if self.restart_count >= self.max_restarts:
            return False
        self.restart_count += 1
        await asyncio.sleep(self.restart_delay)
        return True

audio_restart_manager = AudioRestartManager()

pcs = set()

async def get_audio_track(pc):
    """Get audio track with fallback mechanisms using threading for better performance"""
    import concurrent.futures
    import threading

    def try_alsa_default():
        try:
            player = MediaPlayer("default", format="alsa")
            return player.audio
        except:
            return None

    def try_alsa_hw():
        capture_cards = get_capture_cards()
        for card in capture_cards:
            try:
                options = {
                    "format": "s16le",
                    "rate": "48000",
                    "channels": "2"
                }
                player = MediaPlayer(f"plughw:{card},0", format="alsa", options=options)
                return player.audio
            except:
                continue
        return None

    def try_subprocess():
        capture_cards = get_capture_cards()
        for card in capture_cards:
            try:
                import subprocess

                # Use ffmpeg directly with ALSA input - bypasses broken Python ALSA
                # ffmpeg captures from detected hw card, resamples, and outputs to stdout
                cmd_ffmpeg = [
                    "ffmpeg",
                    "-f", "alsa", "-i", f"hw:{card},0",
                    "-ar", "48000", "-ac", "1", "-f", "s16le",
                    "-fflags", "nobuffer", "-flags", "low_delay",
                    "-bufsize", "1000",
                    "pipe:1"
                ]
                proc_ffmpeg = subprocess.Popen(
                    cmd_ffmpeg,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL
                )

                # Store process for cleanup
                pc.proc_ffmpeg = proc_ffmpeg

                # Start monitoring task for subprocess health
                asyncio.create_task(monitor_subprocesses(pc))

                player = MediaPlayer(
                    proc_ffmpeg.stdout,
                    format="s16le",
                    options={
                        "sample_rate": "48000",
                        "channels": "1",
                        "fflags": "nobuffer",
                        "flags": "low_delay"
                    }
                )
                return player.audio
            except Exception as e:
                print(f"Subprocess method failed for card {card}: {e}")
                continue
        return None

    # Use threading to try audio sources concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(try_alsa_default),
            executor.submit(try_alsa_hw),
            executor.submit(try_subprocess)
        ]

        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result is not None:
                print("Successfully obtained audio track")
                return result

    print("All audio sources failed, using silence.")
    return SilenceAudioTrack()

async def monitor_subprocesses(pc):
    """Monitor subprocess health and restart if needed"""
    while True:
        await asyncio.sleep(1)  # Check every second

        if hasattr(pc, 'proc_arecord') and pc.proc_arecord.poll() is not None:
            print(f"arecord process died with code {pc.proc_arecord.returncode}")
            # Process died, close connection to trigger cleanup
            pc.close()
            return

        if hasattr(pc, 'proc_ffmpeg') and pc.proc_ffmpeg.poll() is not None:
            print(f"ffmpeg process died with code {pc.proc_ffmpeg.returncode}")
            # Process died, close connection to trigger cleanup
            pc.close()
            return

        # Check if peer connection is still active
        if pc.connectionState in ['closed', 'failed']:
            return

async def index(request):
    return web.FileResponse(os.path.join(AUDIO_DIR, "client.html"))

async def offer(request):
    # Handle preflight OPTIONS request
    if request.method == 'OPTIONS':
        response = web.Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("iceconnectionstatechange")
    async def on_ice_state_change():
        print("ICE state:", pc.iceConnectionState)
        if pc.iceConnectionState in ["failed", "closed"]:
            # Terminate any running audio processes
            if hasattr(pc, 'proc_arecord') and pc.proc_arecord:
                pc.proc_arecord.terminate()
                pc.proc_arecord.wait()
            if hasattr(pc, 'proc_ffmpeg') and pc.proc_ffmpeg:
                pc.proc_ffmpeg.terminate()
                pc.proc_ffmpeg.wait()
            await pc.close()
            pcs.discard(pc)

    # Use microphone as audio source
    audio = await get_audio_track(pc)

    pc.addTrack(audio)

    await pc.setRemoteDescription(offer)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    response = web.json_response(
        {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
    )
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

async def cleanup(app):
    for pc in pcs:
        # Terminate any running audio processes
        if hasattr(pc, 'proc_arecord') and pc.proc_arecord:
            pc.proc_arecord.terminate()
            pc.proc_arecord.wait()
        if hasattr(pc, 'proc_ffmpeg') and pc.proc_ffmpeg:
            pc.proc_ffmpeg.terminate()
            pc.proc_ffmpeg.wait()
        await pc.close()

async def status(request):
    """Check if audio server is running"""
    return web.json_response({
        "status": "running",
        "port": 9000,
        "active_connections": len(pcs)
    })

app = web.Application()
app.on_shutdown.append(cleanup)
app.router.add_get("/", index)
app.router.add_get("/status", status)
app.router.add_post("/offer", offer)
app.router.add_options("/offer", offer)  # Handle preflight OPTIONS

async def run_audio_server():
    print("=" * 50)
    print("Audio Stream Server starting on port 9000")
    print("=" * 50)
    
    # Print available audio devices
    print("\nAvailable audio devices:")
    devices = get_available_audio_devices()
    if devices:
        for d in devices:
            print(f"  - {d}")
    else:
        print("  (No devices found)")
    
    print("\nTrying audio sources in order:")
    print("  1. ALSA default")
    print("  2. ALSA hw:2,0")
    print("  3. Subprocess (arecord -> ffmpeg)")
    print("=" * 50)
    
    await web._run_app(app, host="0.0.0.0", port=9000)

if __name__ == "__main__":
    asyncio.run(run_audio_server())