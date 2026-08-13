# Remote Lab DESE — session handoff

Paste this file's content (or point Claude at this path) as the first message in a new chat to continue where this one left off. Written for someone/something with zero prior context.

## The project
An institutional remote electronics lab. Students book and remotely operate real hardware (Arduino/ESP32/STM32 boards) — serial data, oscilloscope, camera, firmware flashing. Scaling toward 100+ physical units.

Two repos, two roles:
- **`lab-pi`** (this repo) — runs on each individual "Lab Pi" (one Raspberry Pi per physical experiment). Currently a full Flask app: hardware I/O *and* renders the pages students see.
- **`remote_lab_admin`** — the central machine. Call it the **Master PC**, not "Admin Pi" — it's not just an admin panel. It has three jobs: students **book** experiments on it, students **conduct** experiments through it (this is growing, see the migration plan below), and it handles **admin** management (registering Lab Pis, assigning experiments, UI config). It must eventually run on real server hardware, not a Pi — a Pi can't handle rendering/relaying for 100+ concurrent sessions.

Both repos are on GitHub (`Abhilash1575/remote_lab_pi` and `Abhilash1575/remote_lab_admin`).

## Security hardening already done (this session)
**Lab Pi (`lab-pi`)**:
- Random per-Pi Flask secret key (was hardcoded `'devkey'`, shared across every Pi)
- Fixed command injection in `/flash` and `/factory_reset` (shell string execution → argv lists)
- Session required before flash/factory-reset will run (previously worked with zero active session)
- Fixed missing `scipy` dependency in `install-lab-pi.sh` (was in `requirements.txt` but never actually installed — app crashed on fresh install)
- Fixed `systemd/audio_stream.service` (had a real hardcoded username/path from someone's dev machine, plus pointed at the system Python instead of the project's venv — audio would never have worked on a fresh install)

**Master PC (`remote_lab_admin`)**:
- Removed hardcoded `SECRET_KEY`, Gmail App Password, and Google OAuth Client Secret from source — now environment-variable-only. **The old values were exposed in this repo's git history on a public GitHub repo — the Gmail App Password and OAuth Client Secret need rotating (Google Account settings / Google Cloud Console) if that hasn't happened yet.** Ask the user before assuming it's done.
- Added CSRF protection across every form and JS `fetch()` call
- Fixed 5 destructive routes (delete device/experiment/user/booking/session) that only required a GET request — exploitable via a bare link or image tag, no click needed. Now POST-only.
- Added rate limiting to login/signup/password-reset
- `/api/lab-pi/<id>/command` (starts/stops sessions, toggles relays on any Lab Pi) had **no authentication at all** — now requires admin login
- Replaced the Flask dev server with gunicorn
- Disabled public self-signup and Google-OAuth auto-account-creation — login is now restricted to accounts an admin created via the existing "Bulk Upload Users" CSV feature (which already emails each person a link to set their own password)
- **Mutual authentication (`MASTER_API_KEY`) now enforced both directions**: Admin→Lab Pi commands (session-start/end/update-config) and Lab Pi→Admin (register/heartbeat) each verify an `X-Master-Api-Key` header, closing a spoofing gap where either side could previously be impersonated.

## Outstanding action items — check with the user before assuming these are done
1. Rotate the leaked Gmail App Password and Google OAuth Client Secret.
2. Push `lab-pi`'s changes to GitHub — as of this session's end, that repo had local commits/edits not yet pushed (unlike `remote_lab_admin`, which was pushed).
3. Confirm `MASTER_API_KEY` is the *same exact value* in the Master PC's `.env` and every Lab Pi's `.env` — a mismatch silently breaks Admin↔Lab Pi communication (fails safe with a warning if unset, but a wrong value hard-fails).
4. End-to-end test after deploying: login, booking, admin CRUD actions, and specifically Lab Pi registration/heartbeat (the part most likely to break silently, since it's not visible in a browser).

## Architecture work: planned, not yet built
- **Golden-image / zero-touch fleet provisioning**: discussed conceptually (MAC-derived Lab Pi ID, first-boot script regenerating SSH host keys/machine-id/secrets so a cloned image doesn't duplicate them, no domain needed for the imaging step itself). No scripts written yet.
- **Reverse-proxy/HTTPS gateway** (so only the Master PC is internet-facing): draft at `remote_lab_admin/install/reverse-proxy-setup.md`. Not implemented — waiting on a domain name.
- **Full UI migration** (institutional IT requirement: Lab Pi must not serve pages at all, only the Master PC renders anything a browser sees): complete written plan at `lab-pi/MASTER_UI_MIGRATION_PLAN.md`. This is a bigger, harder project than the reverse-proxy one — read that file before starting any implementation, especially the section on the one architectural decision it all hinges on (browser talks only to the Master PC, which itself connects to each Lab Pi — not a per-Pi reverse-proxy subdomain).
- **IT-facing one-pager**: `lab-pi/IT_HANDOFF_SUMMARY.md` — architecture/security/data summary + specific asks (firewall, domain, TLS guidance) to hand to the institution's IT/network team.

## UI redesign — visual direction explored, NOT yet applied to the codebase
Explored an "industrial-grade" visual redesign for the main experiment control page (current look was rejected as "AI cartoonish"). Built as standalone mockups (published as Claude Artifacts, links may or may not still be live — ask the user), matching the *exact* existing layout of `templates/index.html` (header bar, left column with 4 cards: Board & Firmware / Quick Actions / Serial Monitor / Dynamic Controls, right column with 2 cards: Chart / Video Feed) but restyled: graphite + amber industrial instrument-panel palette, Big Shoulders + IBM Plex Mono typography, both light and dark themes with a live toggle.

**Important**: this was a visual reference only, built and viewed outside the codebase. `templates/index.html` and the rest of the real templates have not been touched — if the direction is approved, it still needs to be actually implemented in the real templates (and likely coordinated with the UI-migration plan above, since that plan will eventually move this page to the Master PC anyway).

## Where things live
- `lab-pi/app.py`, `lab-pi/admin_config.py` — Lab Pi Flask app
- `lab-pi/templates/` — Lab Pi's current (unmigrated, unrestyled) pages
- `lab-pi/install-lab-pi.sh`, `lab-pi/systemd/` — Lab Pi provisioning
- `remote_lab_admin/app.py` — Master PC Flask app (booking, admin, Lab Pi fleet coordination)
- `remote_lab_admin/install/setup_admin_pi.sh` — the canonical Master PC setup script (there's also an `install.sh` that's narrower/incomplete — flagged, not resolved, ask the user which to keep)
