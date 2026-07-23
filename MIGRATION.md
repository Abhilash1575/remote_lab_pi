# Migrating other Lab Pis to the new layout

This repo was restructured to remove duplicate folders and fix a
double-nested `lab-pi/lab-pi/...` layout, and the audio streaming feature
was rewritten from a broken stub into real WebRTC audio (aiortc). Every
other Lab Pi still on the old commit needs a few one-time steps before and
after `git pull` — a plain pull is **not** enough on its own.

## Why a plain `git pull` isn't enough

- Each Lab Pi's customized admin settings (`ui_config.json`,
  `admin_password.hash`) and uploaded content (`uploads/`, `default_fw/`,
  `static/sop/`) live **untracked** inside the old `lab-pi/` subfolder.
  `git pull` only touches tracked files — it will move the code out but
  leave this per-Pi data orphaned at the old path. The new code looks for
  it under `data/` instead, so without migrating it first, admin settings
  silently reset to defaults.
- The **already-installed** systemd unit files (`/etc/systemd/system/vlab-lab-pi.service`,
  `audio_stream.service`) have the old `lab-pi/lab-pi/...` path baked in.
  Pulling removes that nested folder, so the very next restart would
  crash-loop with `[Errno 2] No such file or directory` until the unit
  files are patched.
- Real WebRTC audio requires `av` (PyAV) in the venv. `install-lab-pi.sh`
  used to deliberately skip it (believing it "wasn't needed" and "fails
  to build on aarch64" — neither is true; a prebuilt wheel exists).
  Without it, `audio_stream.service` will crash-loop on import.

## Do not just rerun the full `install-lab-pi.sh`

On an already-configured Pi that's risky for a different reason: it
prompts for `LAB_PI_ID`/`LAB_PI_NAME`/`MASTER_URL` etc. and then
**overwrites `.env` unconditionally**, which can sever that Pi's identity
and registration with the Master Pi. It also redoes `apt upgrade`,
recompiles `ustreamer`, and reinstalls every pip package — slow and
unnecessary just to pick up a folder move. Use the targeted steps below
instead.

## Procedure, per other Lab Pi

### 1. Copy the fix script over and run it — BEFORE pulling

```bash
scp pi2@<this-pi-ip>:/home/pi2/lab-pi/fix_services_pre_pull.sh pi2@<other-pi>:~/lab-pi/
ssh pi2@<other-pi>
cd ~/lab-pi
./fix_services_pre_pull.sh
```

This script (checked into the repo root) does four things, all safe to
run before pulling and all idempotent if interrupted partway:

1. Stops `vlab-lab-pi.service` (only if installed) so nothing writes to
   the admin-settings files mid-migration.
2. Migrates `lab-pi/ui_config.json`, `lab-pi/admin_password.hash`,
   `lab-pi/uploads/`, `lab-pi/default_fw/`, `lab-pi/static/sop/` into the
   new `data/` folder at the repo root. Each move only happens if the old
   path exists and the new one doesn't, so it never clobbers anything.
3. Patches whichever of `vlab-lab-pi.service` / `audio_stream.service`
   are actually installed, collapsing the doubled `lab-pi/lab-pi/` path
   and resolving `<LOCAL_USER>`/`%h`/`%i` placeholders to that Pi's real
   username — handles whichever broken variant that Pi ended up with
   historically. Editing the unit file + `daemon-reload` does **not**
   restart anything currently running, so there's no downtime window
   from this step.
4. Installs `av` (PyAV), pinned to `av>=14.0.0,<17.0.0` (the range
   aiortc actually supports — newer `av` releases have breaking API
   changes). Downloads a prebuilt aarch64 wheel; no compilation needed.

It never touches `.env` and does no `apt`/system package changes.

### 2. Pull and restart

```bash
git pull
sudo systemctl start vlab-lab-pi.service
sudo systemctl restart audio_stream.service
sudo systemctl status vlab-lab-pi.service audio_stream.service --no-pager
```

### 3. Confirm the microphone was detected correctly

```bash
sudo journalctl -u audio_stream.service -n 20 --no-pager
```

Look for a line like:

```
[WebRTC] Using audio capture device: plughw:2,0
```

The device is auto-detected via `arecord -l` (the first capture-capable
card found) — this should generally just work. If a Pi has more than one
capture device and it picks the wrong one, or the log says `default`
because nothing was found, add to that Pi's `.env`:

```
AUDIO_INPUT_DEVICE=plughw:N,M
```

(find the right `N,M` by running `arecord -l` on that specific Pi), then
restart `audio_stream.service` again.

## Things that do NOT need per-Pi attention

- **The broken vendored ALSA config path.** The aarch64 `av` wheel bundles
  a static ALSA library compiled with a hardcoded config path from its own
  build environment (`/tmp/vendor/share/alsa/...`). Without a real
  `alsa.conf` tree at that exact path, `av.open()` fails for *every*
  device string, including `default`. `Audio/server.py` fixes this itself
  by symlinking the real system config into place on every process
  startup — no manual step, no systemd config, works regardless of how
  the process is launched, and survives reboots since it's recreated
  fresh each start (`/tmp` is wiped on reboot).
- **`.env`** — never touched by any of this.
- **System packages** — no `apt` changes needed, just the one `pip install av`
  (a wheel download, not a source build).

## Expected, non-bug failure case

If a Lab Pi genuinely has no microphone attached, `audio_stream.service`
will fail with `No audio track available from input device "..."` in the
log. That's an honest failure for that case, not a bug — audio just stays
off for that Lab Pi until a mic is attached.

## Reference: what actually changed (for context)

- Removed duplicate folders (`templates/`, `sound/`, `Oscilloscope/`,
  `lab-pi/mylastworkcodes/`, orphaned `audio_capture.py`/`config.py`).
- Flattened the double-nested `lab-pi/lab-pi/...` layout — code now lives
  directly at the repo root (`app.py`, `admin_config.py`, `Audio/`, etc.).
- Consolidated all systemd unit files into `systemd/` (previously spread
  across the repo root, `services/`, and `Audio/services/`).
- Consolidated per-Pi runtime state into `data/` (`ui_config.json`,
  `admin_password.hash`, `uploads/`, `default_fw/`, `sop/`).
- Rewrote `Audio/server.py`'s `/offer` handler from a broken hand-rolled
  SDP stub (missing mandatory `v=`/`o=` lines, no real media backend) into
  a real `aiortc.RTCPeerConnection` backed by the Lab Pi's actual
  microphone.
- Fixed `templates/audio.html` pointing at the wrong port (9002 instead
  of the real 9000).
