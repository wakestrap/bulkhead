# Bulkhead — Subsea Engineering Dashboard

Flask webapp at `http://192.168.2.12:5050`. Dark-themed tabbed UI with subsea/EE tools.

## Architecture

```
/home/hermes/Bulkhead/
├── app.py                  # Flask backend (Flask 3.1.3, Python 3.10)
├── templates/index.html    # All HTML/CSS/JS in one Jinja template
├── config.json             # User settings (tab visibility, timezones)
├── .tv_token               # Samsung TV auth token
└── static/                 # Standalone tool HTMLs + assets
```

- **Backend**: Python 3.10 ONLY — `/usr/bin/python3.10`. System `python3` is 3.11 in Hermes venv, Flask not installed there.
- **Frontend**: Vanilla JS in `<script>` block of `index.html`. Tabs vie `<div class="tab-content" id="tab-XXX">` + `<button class="tab-btn" data-tab="XXX">`.
- **Config**: `config.json` on disk, GET/POST `/api/config`, merged with `DEFAULT_CONFIG` in app.py on load.
- **Production mode**: No auto-reload. Template changes need restart.

## Running It

```bash
# Restart (systemd — preferred):
systemctl --user restart bulkhead.service

# Restart (manual fallback):
kill $(pgrep -f 'app.py') 2>/dev/null
cd /home/hermes/Bulkhead && /usr/bin/python3.10 app.py --host 0.0.0.0 --port 5050

# Verify:
curl -s -o /dev/null -w '%{http_code}' http://192.168.2.12:5050/
```

## Existing Tabs (9 total)

| Tab | ID | Type | Backend Routes |
|-----|----|------|----------------|
| World Clock | `clock` | Inline JS | None |
| Depth ↔ Pressure | `depth` | Inline JS | `/api/depth` |
| Resistor Decoder | `resistor` | Inline JS | `/api/resistor/decode`, `/api/resistor/lookup` |
| TV Remote | `tv` | Inline JS | `/api/tv/status`, `/api/tv/key`, `/api/tv/wake` |
| PCB Trace | `pcb` | Inline JS | `/api/pcb/trace` |
| GeoGuessr | `geo` | Inline JS | None |
| Battery Pack | `battery` | Static iframe | None (`static/battery-solver.html`) |
| Wire Gauge | `gauge` | Static iframe | None (`static/wire-gauge.html`) |
| Settings | `settings` | Inline JS | `/api/config` |

## Adding a Tab

Two patterns:
- **Pattern A (iframe)**: Drop standalone HTML in `static/`, add `<iframe>` tab-content + button. Used for self-contained tools.
- **Pattern B (inline)**: Add CSS, button, tab-content div, JS functions, init hook. Used for tools with backend routes.

**MANDATORY after adding any tab**: Update BOTH `tabNames` in `index.html` JS (~line 2131) AND `DEFAULT_CONFIG["tabs"]` in `app.py` (~line 572). Without both, tab vanishes on load.

## Pitfalls

- Always use `/usr/bin/python3.10` not `python3`
- Template changes need restart — Flask in production mode
- Don't use `nohup`/`&`/`disown` in foreground terminals — Hermes rejects them
- Tab vanishing on load = forgot to update `DEFAULT_CONFIG` or `tabNames`
- `config.json` corruption via curl POST: send properly JSON-encoded payloads

## Git

Repo: `git@github.com:wakestrap/bulkhead.git`
SSH key: `~/.ssh/id_ed25519_github`
Tag before risky changes, push tags.
