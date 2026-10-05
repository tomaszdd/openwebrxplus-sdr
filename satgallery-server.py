#!/usr/bin/env python3
"""Weather satellite image gallery with a status header (next pass, last
checked, staleness warning) and an enable/disable toggle for the scheduler."""
import http.server
import json
import os
import socketserver
import subprocess
import urllib.parse
from datetime import datetime, timezone

# --- per-device config ---
PORT = 8091
STATUS_FILE = "/var/lib/owrx-sat/next-pass.json"
OUT_DIR = "/var/www/satimages"
SERVICE_NAME = "owrx-satsched"
DEVICE_LABEL = "openwebrxplus"
# --- end per-device config ---

MANIFEST_FILE = os.path.join(OUT_DIR, "manifest.json")
STALE_AFTER_SECONDS = 3600

def svc_active(name):
    r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
    return r.stdout.strip()

def svc_enabled(name):
    r = subprocess.run(["systemctl", "is-enabled", name], capture_output=True, text=True)
    return r.stdout.strip()

def fmt_delta(target):
    secs = (target - datetime.now(timezone.utc)).total_seconds()
    if secs < 0:
        return "in progress"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return (f"{h}h {m}m" if h else f"{m}m {s}s")

def status_html():
    active = svc_active(SERVICE_NAME)
    enabled = svc_enabled(SERVICE_NAME)
    badge_color = {"active": "#3fb950", "inactive": "#98a2b3", "failed": "#f85149"}.get(active, "#98a2b3")
    toggle_action = "sat_off" if enabled == "enabled" else "sat_on"
    toggle_label = "Disable auto-capture" if enabled == "enabled" else "Enable auto-capture"

    try:
        with open(STATUS_FILE) as f:
            d = json.load(f)
    except Exception:
        d = None

    if d is None:
        pass_html = '<span class=dim>No prediction yet.</span>'
        staleness = ""
    else:
        checked = datetime.fromisoformat(d["checked"])
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        stale = age > STALE_AFTER_SECONDS
        staleness = (f'<span style="color:#f5a623">&#9888; last checked {int(age // 60)}m ago '
                     f'&mdash; {DEVICE_LABEL} may be offline</span>') if stale else \
                    f'<span class=dim>last checked {int(age // 60)}m ago</span>'
        state = d.get("state")
        if state == "scheduled":
            aos = datetime.fromisoformat(d["aos"])
            los = datetime.fromisoformat(d["los"])
            pass_html = (f'<b>{d["satellite"]}</b> &mdash; AOS {aos.strftime("%H:%M UTC")} '
                         f'&middot; LOS {los.strftime("%H:%M UTC")} '
                         f'<span class=dim>({fmt_delta(aos)} away)</span>')
        elif state == "capturing":
            los = datetime.fromisoformat(d["los"])
            pass_html = f'<b>{d["satellite"]}</b> &mdash; <span style="color:#3fb950;font-weight:700">capturing now</span>, done in {fmt_delta(los)}'
        elif state == "no_pass":
            pass_html = '<span class=dim>No pass above the elevation threshold right now.</span>'
        elif state == "no_tle":
            pass_html = '<span class=dim>Waiting on satellite tracking data.</span>'
        else:
            pass_html = '<span class=dim>Unknown state.</span>'

    return f"""<div class=card>
<div><b>Scheduler:</b> <span style="color:{badge_color};font-weight:700">{active}</span>
<span class=dim>({enabled})</span></div>
<div style="margin-top:6px">{pass_html}</div>
<div style="margin-top:4px">{staleness}</div>
<form method=post action=/action style="margin-top:10px">
<button name=action value={toggle_action}>{toggle_label}</button>
</form>
</div>"""

def render_page(msg=""):
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{DEVICE_LABEL} sat images</title><style>
body{{font-family:system-ui,sans-serif;background:#0e1116;color:#e6e9ef;margin:0;padding:16px;max-width:900px;margin:auto}}
h1{{font-size:20px}}a{{color:#f59e0b}}.hint,.dim{{color:#98a2b3;font-size:12.5px}}
.card{{background:#171d2b;border:1px solid #28303f;border-radius:10px;padding:12px 14px;margin:10px 0;font-size:14px}}
button{{background:#f59e0b;color:#0e1116;border:0;border-radius:8px;padding:8px 14px;font-size:13px;font-weight:700;cursor:pointer}}
.msg{{background:#1c2a1c;border:1px solid #2e5c2e;border-radius:8px;padding:8px 12px;margin-bottom:10px;font-size:13px}}
.grid{{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}}
.c{{background:#171d2b;border:1px solid #28303f;border-radius:10px;padding:8px;width:220px;cursor:pointer}}
.c img{{width:100%;border-radius:6px;display:block;background:#000}}
.c .n{{font-size:12px;color:#98a2b3;margin-top:6px}}.c .d{{font-size:11px;color:#5b6472}}
.lb{{position:fixed;inset:0;background:#000c;display:none;align-items:center;justify-content:center;z-index:5;padding:20px;cursor:zoom-out}}
.lb.open{{display:flex}}.lb img{{max-width:100%;max-height:100%;border-radius:6px}}
</style></head><body>
<h1>&#128752; Satellite Images <a href="/" style="font-size:13px;float:right">&larr; refresh</a></h1>
{f'<div class=msg>{msg}</div>' if msg else ''}
{status_html()}
<div class=hint id=st>loading&hellip;</div>
<div class=grid id=grid></div>
<div class=lb id=lb onclick="this.classList.remove('open')"><img id=lbimg></div>
<script>
fetch('/data/manifest.json').then(r=>r.json()).then(items=>{{
 const st=document.getElementById('st'),grid=document.getElementById('grid');
 if(!items.length){{st.textContent='no images captured yet.';return;}}
 st.textContent=items.length+' image(s) captured';
 grid.innerHTML=items.map(it=>`<div class=c onclick="openImg('${{it.file}}')"><img loading=lazy src="/data/${{it.file}}"><div class=n>${{it.satellite}}</div><div class=d>${{new Date(it.date).toLocaleString()}}</div></div>`).join('');
}}).catch(()=>{{document.getElementById('st').textContent='no images yet.';}});
function openImg(f){{document.getElementById('lbimg').src='/data/'+f;document.getElementById('lb').classList.add('open');}}
</script></body></html>"""

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=OUT_DIR, **kwargs)

    def log_message(self, *a):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            body = render_page().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path.startswith("/data/"):
            self.path = parsed.path[len("/data"):] or "/"
            return super().do_GET()
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        fields = urllib.parse.parse_qs(self.rfile.read(length).decode())
        action = fields.get("action", [""])[0]
        if action == "sat_on":
            subprocess.run(["systemctl", "enable", "--now", SERVICE_NAME], capture_output=True)
            msg = "Weather satellite auto-capture enabled."
        elif action == "sat_off":
            subprocess.run(["systemctl", "disable", "--now", SERVICE_NAME], capture_output=True)
            msg = "Weather satellite auto-capture disabled."
        else:
            msg = "unknown action"
        body = render_page(msg).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    srv = Server(("0.0.0.0", PORT), Handler)
    print("sat gallery server on :{}".format(PORT), flush=True)
    srv.serve_forever()
