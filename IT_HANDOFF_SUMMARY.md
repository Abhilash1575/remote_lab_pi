# Remote Lab Platform — Technical & Security Overview
*Prepared for IT/network review before institutional rollout*

## What this is
A remote lab platform letting students run real hardware experiments (Arduino/ESP32/STM32 etc.) over the network. One Raspberry Pi ("Lab Pi") per physical experiment, currently scaling toward 100+. One central machine ("Admin PC") handles booking, login, and fleet coordination.

## Architecture
- **Admin PC**: Flask-based web app. Handles student login/booking, admin management (devices, experiments, users), and coordinates the Lab Pi fleet (assigns sessions, receives heartbeats/status).
- **Lab Pi fleet**: each Pi runs its own small web app serving one experiment's live controls (camera, oscilloscope, serial console, firmware flashing) directly to whichever student has an active booking.
- **Planned change (what we need IT's help with)**: today, each Lab Pi is reachable directly. We want to move to a model where **only the Admin PC is exposed to the outside network**, reverse-proxying to each Lab Pi over a private internal network — so a security review only needs to examine one gateway plus one shared Pi image, not 100+ individual network endpoints. See "Requests for IT" below.

## Data handled
- Student/staff: email address, login timestamps, IP address, booking history.
- Auth: Google OAuth, or email/password (bcrypt-hashed, never stored in plain text).
- Experiment session logs (which board, which Pi, duration).
- Stored in a single SQLite database file on the Admin PC. **No automated backups configured yet** — flagged as an open item below.

## Security work completed
This system went through a focused security pass before this rollout discussion. Concretely:
- Removed all hardcoded secrets from source code (Flask session-signing keys, mail account password, Google OAuth client secret) — now loaded from environment variables; any credential that had been committed to source control was rotated.
- Fixed a command-injection vulnerability in the firmware-flashing feature (user-influenced input was being run through a shell rather than passed as a fixed argument list).
- Closed an endpoint that allowed starting/stopping experiment sessions and toggling hardware relays on any Lab Pi with **no authentication at all**.
- Added CSRF protection across every form and AJAX action in the admin panel.
- Found and fixed 5 destructive actions (delete device/experiment/user/booking/session) that were triggerable via a plain GET request — meaning a malicious link or embedded image could trigger them on a logged-in admin's browser without any click. Converted to properly-protected POST requests.
- Added rate limiting on login, signup, and password-reset to block brute-force attempts.
- Added mutual authentication (shared secret key) between the Admin PC and each Lab Pi, so a Lab Pi only accepts session commands that actually came from the real Admin PC.
- Replaced the Flask development server (explicitly not recommended for production use) with a production WSGI server (gunicorn).

## Known limitations / open items (being transparent about what's not done yet)
- **No HTTPS yet.** All traffic is currently plain HTTP. This is the main thing blocking us pending a domain and reverse-proxy setup — see requests below.
- **No domain restriction on Google OAuth login** — currently any Google account can sign up, not just institutional accounts.
- **SQLite with no automated backups.** Fine for a pilot; a real institutional deployment likely needs a proper backup/recovery plan.
- **No centralized audit logging.** Admin actions currently go to console/service logs, not a structured, queryable audit trail.
- **No automated dependency/CVE monitoring.** Library updates are manual.
- **Admin PC is a single point of failure** — if it goes down, the entire fleet is affected. No redundancy currently planned.
- This system controls real electrical hardware (relays powering student experiment boards). We don't currently have a documented worst-case analysis of what a hijacked session could do physically — worth a joint conversation if that matters for your risk assessment.

## Requests for IT
1. **Firewall**: only port 443 (HTTPS) needs to be reachable from outside on the Admin PC. Nothing else should need to be internet-facing once the reverse-proxy setup is in place.
2. **Domain/subdomain**: we'd like a subdomain (or wildcard subdomain, e.g. `*.lab.yourdomain.edu`) to route to the Admin PC, so each Lab Pi can be addressed individually behind it.
3. **Guidance on TLS termination**: should this terminate on infrastructure you control (a WAF/edge proxy), or is a reverse proxy on the Admin PC itself (e.g. Caddy, auto-provisioning Let's Encrypt certs) acceptable?
4. **Any formal review process** we should be following — security sign-off, an approved VAPT vendor, change-management steps — before this touches the institution's network.

## Contacts
- Technical owner: *[fill in name/contact]*
- Physical location of Admin PC: *[fill in]*
