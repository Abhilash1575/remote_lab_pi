# Lab-Pi API Contract (Phase 1 of MASTER_UI_MIGRATION_PLAN.md)

Derived directly from `app.py` / `admin_config.py` / `templates/*.html` as they exist today. Every
endpoint below either already exists (marked **existing**) or replaces a `render_template(...)` call
or a browser-facing SocketIO event (marked **new**). Nothing here is implemented yet except the
**existing** rows — this is the design to build against in Phase 2.

## Hardware model this contract assumes

`current_session_key`, `current_board_type`, `current_experiment_id`, `current_sop_file`, the serial
`serial_connections` dict, and the oscilloscope worker's history buffer are all **process-global**, not
per-session. A Lab Pi is one physical board attached to one Raspberry Pi — it does not serve multiple
concurrent independent experiments. `active_sessions` being a dict (today) is about clean expiry
bookkeeping, not multi-tenancy. Every endpoint below inherits that assumption: `session_key` is
validated against "is this the currently active session," not "which of N sessions."

## Auth model

Three tiers, matching the pattern already established by `_verify_master_request()` /
`X-Master-Api-Key` (`app.py:227`, both directions already wired per `SESSION_HANDOFF.md`):

| Tier | Who calls it | Mechanism |
|---|---|---|
| **A — Master-authenticated** | Master PC backend only, never a browser | `X-Master-Api-Key` header, `hmac.compare_digest` against `MASTER_API_KEY` env var. Reuses `_verify_master_request()` as-is. |
| **B — Master-authenticated + session-scoped** | Master PC backend, on behalf of an active student session | Same `X-Master-Api-Key` header, **plus** a `session_key` field in the request body/query. Lab Pi checks it against `active_sessions` / `current_session_key` exactly as it does today, just reading the value from the request instead of a browser cookie. |
| **C — Master-only SocketIO client** | Master PC's SocketIO *client* connection to this Lab Pi | Lab Pi's SocketIO server stops accepting browser connections; the one client that connects presents `MASTER_API_KEY` on the `connect` handshake (`auth={'key': ...}` on the client, checked in the `connect` handler on the server, reject if missing/wrong). |

Every row in the tables below is tagged **A**, **B**, or **C**. Nothing student-facing stays
unauthenticated the way `/toggle_relay`'s `bypass` flag or `/add_session` are today — Tier B closes
that gap by construction, since the Master always has a real `session_key` to attach.

---

## 1. Experiment control page (today: `/`, `/experiment`, `/homepage` → `index.html`)

| Endpoint | Tier | Replaces | Notes |
|---|---|---|---|
| `GET /api/ui-config` | B | `get_student_ui_config()` passed into every `render_template` | Returns exactly what `get_student_ui_config()` returns today: `{controls, defaults, required_controls, serial_ports, experiment_name, updated_at}`. Master calls this once per page load and again whenever it needs to refresh (see `ui_config_updated` under §5). |
| `GET /api/ports` | B | `list_serial_ports()` via `list_ports` socket event / `GET /ports` | `{ports: ["/dev/ttyUSB0", ...]}`. Existing `GET /ports` (`app.py:1272`) already returns this shape unauthenticated — becomes Tier B. |
| `POST /api/serial/connect` | B | `connect_serial` socket event | Body: `{conn_id, port, baud, session_key}`. Response: `{status: "connected"|"error", port, baud, conn_id, message?}` — same shape `serial_status` emits today. Gate: `is_control_enabled('serial_connect')`, unchanged. |
| `POST /api/serial/disconnect` | B | `disconnect_serial` socket event | Body: `{conn_id, session_key}`. Response: `{status: "disconnected", conn_id}`. |
| `POST /api/command` | B | `send_command` socket event | Body: `{conn_id?, cmd, session_key}`. Writes to serial exactly as `handle_send_command` does. Response: `{status: "sent"|"no-serial"|"error", message?}` — today this is fire-and-forget over `feedback`; make it a real response since there's no persistent socket to a browser anymore. |
| `POST /api/serial/reset` | B | `reset_serial` socket event | Body: `{conn_id?, port?, baud?, session_key}`. Same DTR/RTS pulse logic (`app.py:1748`), same "works with or without an existing connection" behavior. Response: `{status: "ok"|"error", message}`. |
| `POST /api/waveform` | B | `waveform_config` socket event | Body: `{shape, freq, amp, session_key}`. Response: `{status: "ok"|"error"}`. |
| `POST /api/relay` | B | `/toggle_relay` | Body: `{state: "on"|"off", session_key}`. **Drop the existing `bypass` flag** — Tier B's Master-authenticated + session_key check replaces the need for a bypass path entirely; nothing should be able to skip session validation once the Master is the only caller. Response: `{status: "on"|"off"|"error", message?}`. |
| `POST /api/flash` | B | `/flash` | Multipart form: `board, port, firmware` (file) + `session_key`. Same `is_control_enabled('flash_firmware')` / board-lock / `_resolved_flash_port` logic (`app.py:1297`). Response unchanged: `{status, command}`. Progress still streams — see `flashing_status` under §5. |
| `POST /api/factory-reset` | B | `/factory_reset` | Body: `{board, port?, session_key}`. Same `is_control_enabled('factory_reset')` gate and `default_map` lookup (`app.py:1339`). Response: `{status, command}` or `{error, ...}`. |

## 2. Serial chart views (today: `/chart`, `/newchart` → `chart.html`, `newchart.html`)

No new endpoints — these pages only ever needed `GET /api/ui-config` (for `newchart.html`'s
`board_type`/`ui_config`) plus the live `sensor_data` stream, which is a SocketIO relay concern (§5).
`chart.html` takes no server data at all beyond the live stream. `GET /api/latest-sensor-data`
(**existing**, `app.py:1248`) can be kept as-is under Tier B for a poll-based fallback, or dropped once
the SocketIO relay is trusted — Master's call.

## 3. Oscilloscope (today: `/oscilloscope` → `oscilloscope.html`)

| Endpoint | Tier | Replaces | Notes |
|---|---|---|---|
| `GET /api/oscilloscope/settings` | B | implicit (osc_settings global) | Returns current `osc_settings` dict (`trig_v, hyst, rising, samples, smooth, freeze, pre_trigger, trig_src`). |
| `POST /api/oscilloscope/settings` | B | `update_osc_settings` socket event | Body: partial `osc_settings` update, same `.update(data)` semantics (`app.py:1615`). Response: `{status: "ok"}`. |
| `POST /api/oscilloscope/auto-level` | B | `osc_auto_level` socket event | No body needed beyond `session_key`. Triggers the same auto-level calc (`app.py:1621`); result comes back over the `osc_settings_sync` relay event (§5), not the HTTP response — matches today's fire-and-forget pattern. |

Waveform data itself (`osc_data`, ~10 emits/sec, ch1/ch2 arrays) is streaming and belongs in §5, not a
polled REST endpoint — pulling 1000+ samples over HTTP at 10Hz per session doesn't scale to 100+
concurrent sessions the way a relayed socket event does.

## 4. Camera / Audio

Not Flask routes today (`ustreamer` on :8080, `Audio/server.py` WebRTC on :9000 are separate
processes) — no Phase 1 API contract needed here. These are Phase 3's "hard part #2": MJPEG stream
proxy and WebRTC dual-peer-connection relay, designed separately, not as request/response endpoints.

## 5. Admin config CRUD (today: `/admin/settings*` → `admin_settings.html`, rendered *by the Lab Pi*)

All Tier A — only the Master's admin panel calls these, never a browser, so no `session_key` involved.
These extend the Master's existing `/admin/lab-pi/edit/<id>` area per the migration plan, calling
through to the Lab Pi per-device.

| Endpoint | Replaces |
|---|---|
| `GET /api/admin/ui-config` | `GET /admin/settings` (the `cfg = get_effective_ui_config()` half — the **unfiltered** config, including hidden ports' real device paths, since this is admin-only) |
| `POST /api/admin/ui-config` | `POST /admin/settings` main form (`app.py:952`) — controls, `main_view`, dynamic-controls toggle, plotter defaults, experiment name |
| `POST /api/admin/controls` | `/admin/settings/controls/add` |
| `PUT /api/admin/controls/<id>` | `/admin/settings/controls/edit` |
| `DELETE /api/admin/controls/<id>` | `/admin/settings/controls/delete` |
| `POST /api/admin/ports` | `/admin/settings/ports/add` |
| `PUT /api/admin/ports/<id>` | `/admin/settings/ports/edit` |
| `DELETE /api/admin/ports/<id>` | `/admin/settings/ports/delete` |
| `GET /api/admin/port-choices` | `list_admin_port_choices()` — the `/dev/serial/by-id` picker list |

Two things every one of these needs to preserve, not silently drop:
- **The oscilloscope-port conflict check** (`_osc_port_conflict_response`, `app.py:1089`) — reject a
  port profile that collides with `OSC_PORT`, currently returns a re-rendered form with `port_error`;
  as an API it should just be `400 {"error": "..."}`.
- **`sync_serial_profiles()`** (`app.py:195`) currently runs after every ports CRUD op to reconcile live
  connections with the new profile set — must still run server-side inside these handlers, not become
  something the Master is responsible for triggering separately.

Admin **auth** (`/admin/login`, password setup, `session['is_admin']`) itself doesn't move to the Lab
Pi's API — that's Master-side admin login, gating access to *these* endpoints, same as any other
Master admin panel page. Nothing here needs a parallel Lab-Pi-local admin login once pages don't
render on the Lab Pi.

## 6. SocketIO relay (the two live-streaming families, Phase 3 territory but the contract is designed now)

Lab Pi's SocketIO server accepts exactly one client: the Master PC (Tier C, `MASTER_API_KEY` on
connect). The Master needs a `socketio.Client()` per active session, plus its own SocketIO *server*
the browser connects to, re-emitting one side to the other. Event names kept identical
Lab-Pi-side↔Master-side to minimize translation logic; the Master may rename before re-emitting to the
browser if useful.

| Event | Direction | Payload | Today's emitter/consumer |
|---|---|---|---|
| `sensor_data` | Lab Pi → Master | `{conn_id, port, data}` | `send_sensor_data_to_clients` (`app.py:1820`), parsed out of serial lines in `serial_reader_worker` |
| `feedback` | Lab Pi → Master | `{conn_id, text}` | Raw serial line echo + status strings (connect/reset/send errors) |
| `serial_status` | Lab Pi → Master | `{status, port?, baud?, conn_id, message?}` | Result of connect/disconnect |
| `ports_list` | Lab Pi → Master | `["/dev/ttyUSB0", ...]` | On connect + `list_ports` |
| `flashing_status` | Lab Pi → Master | string (raw subprocess output line) | `run_flash_command` (`app.py:1321`) |
| `osc_data` | Lab Pi → Master | `{ch1[], ch2[], triggered, ts, ch1_vmin, ch1_vmax, ch1_vpp, ch1_freq, ch1_dc, ch2_...}` | `osc_worker`, ~10Hz |
| `osc_settings_sync` | Lab Pi → Master | `{trig_v}` (currently only field synced back) | Result of auto-level |
| `board_type_updated` | Lab Pi → Master | `{board_type}` | Emitted from `/api/lab-pi/update-config` today — stays as-is, Master already triggers this itself so it may just update its own state directly instead of round-tripping |
| `ui_config_updated` | Lab Pi → Master | full `get_student_ui_config()` payload | Emitted after every admin CRUD op today (`app.py:974` etc.) — once CRUD moves to Master-side API calls (§5), the Master can update its own cached copy directly on a successful API response instead of needing this event pushed back. **Candidate to drop** once §5 is REST-based. |

Client→server events (`connect_serial`, `send_command`, etc.) are **not** relayed sockets — those
already became Tier B REST calls in §1/§3 above (a request/response action doesn't need a persistent
bidirectional channel; only the Lab-Pi-initiated push events above do).

## Dead code — do not carry forward

`templates/experiment.html` is not rendered by any route (verified: no `render_template('experiment.html')`
anywhere in `app.py`) and its only SocketIO usage, `experiment_command`, has no matching
`@socketio.on('experiment_command')` handler on the server. It appears to be an orphaned earlier
version of the experiment page. Confirm with whoever owns this repo that it's safe to delete rather
than migrate — don't spend Phase 2 effort building a Master-side equivalent for a page nothing
currently reaches.

## Open questions this phase should resolve before Phase 2 starts

1. **`GET /api/command` response semantics** — today `send_command` is fire-and-forget over a
   persistent socket (the `feedback` event arrives async, possibly after the HTTP response already
   returned in the new model). Decide: does `POST /api/command` block briefly for a serial ack, or
   return immediately and let `feedback` (relayed) carry the real result? Recommend: return immediately,
   matching current behavior, and let the relay carry `feedback`/`sensor_data` — avoids adding latency
   to every keystroke/slider-drag command.
2. **`ui_config_updated` relay vs. direct Master-side update** (see §5/§6) — once admin CRUD is REST
   calls the Master itself makes, does the Lab Pi still need to push `ui_config_updated` back over the
   relay, or does the Master just update its own cache on a successful `POST/PUT/DELETE` response?
   Simpler to drop the push and rely on the response, but only safe if nothing else can change the
   config concurrently (nothing does today — Lab Pi has no other admin entry point once this migrates).
3. **Rate limiting** on the new Tier B endpoints — `POST /api/command` in particular gets called at
   slider-drag frequency; confirm the Master's outgoing call pattern (debounce client-side, or let
   every call through) before it hits 100+ Lab Pis' worth of aggregate traffic.
