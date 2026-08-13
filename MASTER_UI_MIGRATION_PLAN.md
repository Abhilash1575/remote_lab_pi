# Plan: Move all pages off the Lab Pi, onto the Master PC

## Context for whoever picks this up
Institutional IT reviewed the architecture and requires: **Lab Pis must not serve web pages.** Today each Lab Pi (`lab-pi` repo, `app.py`) is a full Flask app that both operates the hardware *and* renders/serves the HTML a student's browser sees (Jinja templates, sessions, everything). IT wants that split: Lab Pi does hardware I/O only and hands back data; the central machine is the only thing that ever builds a page for a browser.

**Terminology correction, important for anyone continuing this**: the central machine is the **Master PC** — not "Admin Pi," and not just "admin." Two things about that name:
- **It must be a real PC/server, not a Raspberry Pi.** A Pi (even a Pi 5) doesn't have the CPU, RAM, or network throughput to render pages and relay live video/audio/data for 100+ *simultaneous* experiment sessions once this migration is done — that's an entirely different load profile than the booking/admin traffic it currently handles. Sizing this machine properly (cores, RAM, NIC bandwidth — camera+audio relay for 100 concurrent students is real bandwidth) is part of this plan, not an afterthought.
- **"Admin" undersells what it does.** The Master PC has three roles, not one: (1) students **book** experiments on it, (2) students **conduct** experiments through it — this is the new, heavy part this migration adds, since it becomes the thing rendering every live page and relaying every stream, and (3) **admin management** — modifying UI config, registering Lab Pis, assigning experiments to them. Admin is the smallest of the three. The existing repo is still named `remote_lab_admin` and code will still say "Admin Pi" in places (`MASTER_URL`, `admin_config.py`, etc.) — that's existing naming debt, not something this plan requires renaming, but don't let the old name narrow how this gets designed.

This is a bigger change than the earlier "put a reverse proxy in front of the fleet" plan (see `install/reverse-proxy-setup.md` in the admin repo) — that plan only hid Lab Pi *network addresses*, while the Lab Pi still rendered its own pages underneath the proxy. This plan is about removing page-rendering from the Lab Pi's code entirely.

## The one architectural decision to make first (read this before writing any code)

IT's phrasing — "pages will be on master system, **where this py connects to**" — implies the *Master PC's own Python backend* initiates connections to each Lab Pi, not the student's browser (even via a proxy). That's a specific design, and it's the one this plan assumes:

- **Browser talks to exactly one hostname, ever: the Master PC.** No per-Lab-Pi subdomains, no browser JS/WebSocket/fetch call that targets a Lab Pi's address directly, even indirectly through a proxy hostname.
- **The Master PC's backend code makes its own outgoing calls** (HTTP requests, a SocketIO client connection, a proxied video stream) to whichever Lab Pi is assigned to the active session, over the private internal network, and relays the result back to the browser through the Master's own connection.
- **This actually simplifies the earlier network plan**: no wildcard DNS, no per-Pi TLS cert, no per-Pi Caddy block needed. Just one hostname for the Master PC; Lab Pis sit on a private network reachable only by the Master's backend (a simple firewall rule per Lab Pi: accept port 10000 only from the Master's IP — already drafted in `reverse-proxy-setup.md`).

If a future engineer decides browser-direct-via-per-Pi-subdomain is acceptable instead (i.e. only hiding raw IPs, not fully removing pages from the Pi), that's the *other* plan (`reverse-proxy-setup.md`) — flag that explicitly with IT before mixing the two, since they imply different network setups.

## What currently lives on the Lab Pi that has to move

From `lab-pi/app.py` and `lab-pi/templates/`, everything that calls `render_template(...)`:

| Feature | Current Lab Pi route(s) | Template(s) |
|---|---|---|
| Main experiment control (serial plotter, dynamic controls, board select) | `/`, `/experiment`, `/homepage` | `index.html` |
| Serial chart views | `/chart`, `/newchart` | `chart.html`, `newchart.html` |
| Oscilloscope | `/oscilloscope` | `oscilloscope.html` |
| Camera view | `/camera` | `camera.html` |
| Local admin login/settings (which controls are enabled, serial port profiles) | `/admin/login`, `/admin/settings`, `/admin/settings/*` | `admin_login.html`, `admin_settings.html` |
| Firmware flashing UI | (embedded in `index.html` / `flash.html`) | `flash.html` |
| Expired session message | shown by several routes above | `expired_session.html` |

Non-page things that stay on the Lab Pi (these are the "hardware I/O" the Master PC will call into):
- Serial port read/write, `active_sessions` tracking, GPIO relay control
- Oscilloscope worker thread (`osc_worker`, history buffer, trigger detection)
- Firmware flash/factory-reset (`run_flash_command` — already hardened against shell injection, see git log)
- `ustreamer` (camera, port 8080) and `Audio/server.py` (WebRTC audio, port 9000) — separate processes already, not Flask routes

## Phase 0 — Size and provision the Master PC

Before any code work: this can no longer be "whatever Pi we had lying around." Needs real specs sized for peak concurrent load (target: 100+ simultaneous students, each with a live camera stream, audio stream, and a SocketIO data feed relayed through it). Get actual hardware (or a properly-sized VM/cloud instance) provisioned and reachable on the private network with the Lab Pi fleet before Phase 2 work starts, so development happens against real conditions, not a laptop.

## Phase 1 — Design the Lab-Pi API contract (do this before touching any code)

Turn every piece of dynamic data/action the templates above currently use into an explicit, documented API endpoint. At minimum, derive these from what the templates already call:
- `GET /api/ui-config` (what `get_student_ui_config()` returns today)
- `GET /api/ports`, `POST /api/serial/connect`, `POST /api/serial/disconnect`
- `POST /api/command` (slider/button → serial write, today's `send_command` socket handler)
- `GET /api/sensor-data` (already exists as `/api/latest-sensor-data` — keep/rename)
- `POST /api/flash`, `POST /api/factory-reset`
- `POST /api/relay` (today's `/toggle_relay`)
- `GET /api/oscilloscope/settings`, `POST /api/oscilloscope/settings`, a way to stream oscilloscope waveform data
- Admin config CRUD: today's `/admin/settings/controls/*` and `/admin/settings/ports/*` become API endpoints the Master PC's admin UI calls per-Pi, instead of pages the Pi itself renders

**Auth model per endpoint** — decide and document explicitly, don't leave implicit:
- Student actions (serial commands, flash, relay): the Master PC already knows the active `session_key`; pass it on each call, Lab Pi validates against its own `active_sessions` (same check that exists today, just moved from "cookie session" to "value the Master sends per request").
- Admin config actions: gate behind the existing `MASTER_API_KEY` (already wired both directions as of the latest security pass — see `_verify_master_request()` / `_verify_lab_pi_request()` in both repos) since only the Master PC should ever call these, never a browser.
- Live/streaming data (sensor readings, oscilloscope waveform, flashing status): today these go over Flask-SocketIO directly to the student's browser. Under this plan, the Lab Pi's SocketIO server should only ever accept a connection from the Master PC (not browsers), and the Master needs its own SocketIO *client* connection to the Lab Pi to receive these events, then re-emits them to the browser over the Master's own SocketIO server. This is real, non-trivial plumbing — budget real time for it, don't treat it as a detail.

## Phase 2 — Build the equivalent pages on the Master PC

For each row in the table above, build the Master-side (`remote_lab_admin`) equivalent:
- New templates in `remote_lab_admin/templates/` (the experiment control page, oscilloscope page, camera page) that look like what students see today, but whose JS calls the Master PC's own routes — which then internally call the Lab-Pi API from Phase 1.
- New Master PC routes that act as the relay: e.g. `GET /session/<key>/sensor-data` on the Master internally does `requests.get(f'http://{lab_pi.ip_address}:10000/api/sensor-data', headers=...)` and returns the result — the browser never sees `lab_pi.ip_address` anywhere.
- Per-Lab-Pi admin settings become a page on the Master PC's existing admin panel (it already has `/admin/lab-pi/edit/<id>` — extend that area rather than inventing a separate settings UI) that reads/writes the Lab Pi's config via the API from Phase 1.

## Phase 3 — The two hard parts (design these explicitly, don't wing it mid-migration)

1. **Live SocketIO relay** (sensor data, flashing status, oscilloscope waveform): Master PC needs a SocketIO client per active session connecting to that session's Lab Pi, forwarding events to the browser. Watch for: cleanup when a session ends (don't leak connections), what happens if a Lab Pi drops mid-session, and load if many sessions are active at once (100+ Pi fleet — the Master PC could end up holding 100 concurrent client connections; this is the load Phase 0's sizing needs to account for).
2. **Camera (MJPEG) and audio (WebRTC) relay**: MJPEG is the easier of the two — Flask can stream-proxy a multipart MJPEG response fairly directly. WebRTC audio is genuinely harder: `aiortc` peer connections are normally negotiated directly between two endpoints, so relaying through the Master PC means it runs *two* peer connections (one to the Lab Pi, one to the browser) and pipes media between them — closer to a small SFU than a simple proxy, and CPU/bandwidth-heavy at 100+ concurrent streams. Flag this to IT as the highest-risk, longest-lead-time, and most hardware-sensitive part of the migration; consider asking whether audio can temporarily stay as a direct (but network-restricted) connection while everything else migrates first.

## Phase 4 — Migration sequence (don't do a big-bang cutover on a live 100+ Pi fleet)

1. Provision and confirm the Master PC (Phase 0) is reachable from a test Lab Pi over the private network.
2. Build and test the new Lab-Pi API endpoints (Phase 1) alongside the existing pages — nothing breaks yet, it's additive.
3. Build the Master PC pages (Phase 2) against a single test Lab Pi, with the old direct-to-Lab-Pi pages still working as a fallback.
4. Migrate one feature at a time (start with something low-risk and non-streaming, e.g. firmware flashing or admin settings) — verify end-to-end on real hardware before moving to the next.
5. Do the two hard parts (Phase 3) last, once the request/response-style features are proven. Load-test the streaming relay with a realistic number of concurrent simulated sessions before trusting it with real students — this is exactly the load Phase 0 sized for; confirm the hardware actually holds up.
6. Only after everything has a working Master PC equivalent: remove the Lab Pi's `render_template()` calls, templates, and Flask-Login/session/admin-page code, and lock down the Lab Pi's firewall to accept connections only from the Master PC's IP (ties back into `reverse-proxy-setup.md`'s firewall step, simplified since there's no per-Pi subdomain to route to anymore).
7. Roll out to the fleet via the imaging pipeline once it's built (see the imaging discussion elsewhere in this project) — don't hand-patch 100+ Pis individually.

## Open questions to resolve with IT before or during implementation
- Confirm the "browser only ever talks to the Master PC" interpretation above is actually what they mean, before Phase 2 work starts — it's the single decision the rest of this plan hangs on.
- What hardware/budget is available for the Master PC — this determines how much headroom Phase 3's relay work has to work with, and whether 100+ concurrent audio/video relay is realistic on one machine or needs to be split across more than one.
- Is a temporary direct (but firewalled) audio connection acceptable while WebRTC relay is built, or is that a hard blocker for sign-off?
- Any existing precedent/library IT expects for this (their own reverse-proxy/media-relay tooling) rather than hand-rolling it?
