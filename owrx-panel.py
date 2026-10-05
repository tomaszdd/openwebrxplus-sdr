#!/usr/bin/env python3
import base64
import os
import http.server
import json
import socketserver
import subprocess
import time
from datetime import datetime, timezone

PORT = 8092
SETTLE_SECONDS = 4
CONFIG_FILE = "/etc/openwebrxplus/panel.conf"
NEXT_PASS_FILE = "/var/lib/owrx-sat/next-pass.json"
HOST_IP = os.environ.get("OWRX_HOST_IP", "192.168.1.50")

def load_auth():
    user, pw = "admin", None
    try:
        for line in open(CONFIG_FILE):
            line = line.strip()
            if line.startswith("PANEL_USER="):
                user = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("PANEL_PASS="):
                pw = line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    if not pw:
        raise SystemExit(
            "No PANEL_PASS set in {}. Create it with:\n"
            "  sudo mkdir -p /etc/openwebrxplus && sudo tee {} <<'EOF'\n"
            "PANEL_USER=admin\n"
            "PANEL_PASS=your-own-password-here\n"
            "EOF".format(CONFIG_FILE, CONFIG_FILE)
        )
    return user, pw

AUTH_USER, AUTH_PASS = load_auth()

SERVICES = ["openwebrx", "sdrconnectserver", "owrx-satsched"]

def svc_active(name):
    r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
    return r.stdout.strip()

def svc_enabled(name):
    r = subprocess.run(["systemctl", "is-enabled", name], capture_output=True, text=True)
    return r.stdout.strip()

def status():
    return {s: {"active": svc_active(s), "enabled": svc_enabled(s)} for s in SERVICES}

def do(cmd):
    subprocess.run(["systemctl"] + cmd, capture_output=True, text=True, timeout=30)

def badge(state):
    color = {"active": "#3fb950", "inactive": "#98a2b3", "failed": "#f85149"}.get(state, "#98a2b3")
    return f'<span style="color:{color};font-weight:700">{state}</span>'

def fmt_delta(target):
    secs = (target - datetime.now(timezone.utc)).total_seconds()
    if secs < 0:
        return "in progress"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return (f"{h}h {m}m" if h else f"{m}m {s}s")

def next_pass_html():
    try:
        with open(NEXT_PASS_FILE) as f:
            d = json.load(f)
    except Exception:
        return '<p class=dim>No prediction yet &mdash; the scheduler needs to be enabled and running to compute this.</p>'
    state = d.get("state")
    if state == "scheduled":
        aos = datetime.fromisoformat(d["aos"])
        los = datetime.fromisoformat(d["los"])
        return (f'<p><b>{d["satellite"]}</b><br>'
                f'AOS {aos.strftime("%H:%M UTC")} &middot; LOS {los.strftime("%H:%M UTC")} '
                f'<span class=dim>({fmt_delta(aos)} away)</span></p>')
    if state == "capturing":
        los = datetime.fromisoformat(d["los"])
        return f'<p><b>{d["satellite"]}</b> &mdash; <span style="color:#3fb950;font-weight:700">capturing now</span>, done in {fmt_delta(los)}</p>'
    if state == "no_pass":
        return '<p class=dim>No pass above the elevation threshold in the next lookout window &mdash; rechecking periodically.</p>'
    if state == "no_tle":
        return '<p class=dim>Waiting on satellite tracking data (TLE fetch) &mdash; will retry automatically.</p>'
    return '<p class=dim>Unknown state.</p>'

def render_page(msg=""):
    st = status()
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta http-equiv=refresh content=60><title>openwebrxplus SDR control</title><style>
body{{font-family:system-ui,sans-serif;background:#0e1116;color:#e6e9ef;margin:0;padding:16px;max-width:640px;margin:auto}}
h1{{font-size:20px}}.card{{background:#171d2b;border:1px solid #28303f;border-radius:10px;padding:14px 16px;margin:12px 0}}
button{{background:#f59e0b;color:#0e1116;border:0;border-radius:8px;padding:12px 16px;font-size:15px;font-weight:700;width:100%;margin:6px 0;cursor:pointer}}
button.sec{{background:#2b3547;color:#e6e9ef}}
.row{{display:flex;gap:8px}}.row>form{{flex:1}}.row button{{width:100%}}
.dim{{color:#98a2b3;font-size:12.5px}}a{{color:#f59e0b}}
.msg{{background:#1c2a1c;border:1px solid #2e5c2e;border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:13px}}
code{{background:#0e1116;border:1px solid #28303f;border-radius:4px;padding:1px 5px;font-size:12.5px}}
table{{width:100%;font-size:13px;border-collapse:collapse}}td{{padding:4px 0}}
</style></head><body>
<h1>&#128225; openwebrxplus SDR control <a href="https://{HOST_IP}" target=_blank style="font-size:13px;float:right;margin-left:14px">&#128225; OpenWebRX+</a><a href="http://{HOST_IP}:8091/" style="font-size:13px;float:right">&#128752; Sat Images</a></h1>
{f'<div class=msg>{msg}</div>' if msg else ''}

<div class=card><b>Status</b><table>
<tr><td>Web-SDR (OpenWebRX+)</td><td>{badge(st['openwebrx']['active'])}</td></tr>
<tr><td>SDRconnect (remote GUI server)</td><td>{badge(st['sdrconnectserver']['active'])}</td></tr>
<tr><td>Weather satellite auto-capture</td><td>{badge(st['owrx-satsched']['active'])} <span class=dim>({st['owrx-satsched']['enabled']})</span></td></tr>
</table></div>

<div class=card><b>Mode</b><p class=dim>The SDRPlay RSP1B is one physical dongle &mdash; only one of these can use it at a time.</p>
<form method=post action=/action><button name=action value=websdr>&#127760; Use Web-SDR (browser, this box's normal mode)</button></form>
<form method=post action=/action><button class=sec name=action value=sdrconnect>&#128421; Use SDRconnect (remote GUI on your PC/Mac)</button></form>
</div>

<div class=card><b>Weather satellite auto-capture</b>
<p class=dim>Waits for Meteor-M2 passes and briefly borrows the dongle from whichever mode is active above, then hands it back. Turn off if you don't want interruptions.</p>
<div class=row>
<form method=post action=/action><button class=sec name=action value=sat_on>Enable</button></form>
<form method=post action=/action><button class=sec name=action value=sat_off>Disable</button></form>
</div></div>

<div class=card><b>&#128752; Next Meteor-M2 pass</b>
{next_pass_html()}
</div>

<div class=card><b>Using SDRconnect</b>
<p class=dim>1. Click "Use SDRconnect" above.<br>
2. Install the SDRconnect desktop app on your own PC/Mac (not this Pi) from <a href="https://www.sdrplay.com/downloads/" target=_blank>sdrplay.com/downloads</a>.<br>
3. In the app, connect to <code>{HOST_IP}:50000</code>.<br>
4. When done, click "Use Web-SDR" above to give the dongle back to the normal browser radio.</p>
</div>

<form method=get><button class=sec>&#8635; Refresh</button></form>
</body></html>"""

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def auth(self):
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                user, pw = base64.b64decode(h[6:]).decode().split(":", 1)
                if user == AUTH_USER and pw == AUTH_PASS:
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="openwebrxplus"')
        self.end_headers()
        return False

    def do_GET(self):
        if not self.auth():
            return
        body = render_page().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.auth():
            return
        length = int(self.headers.get("Content-Length", 0))
        import urllib.parse
        fields = urllib.parse.parse_qs(self.rfile.read(length).decode())
        action = fields.get("action", [""])[0]

        if action == "websdr":
            do(["stop", "sdrconnectserver"])
            time.sleep(SETTLE_SECONDS)
            do(["start", "openwebrx"])
            if svc_enabled("owrx-satsched") == "enabled":
                do(["start", "owrx-satsched"])
            msg = "Web-SDR active. SDRconnect stopped."
        elif action == "sdrconnect":
            do(["stop", "owrx-satsched"])
            do(["stop", "openwebrx"])
            time.sleep(SETTLE_SECONDS)
            do(["start", "sdrconnectserver"])
            msg = "SDRconnect server running on port 50000. Web-SDR and auto-capture stopped so it has the dongle free."
        elif action == "sat_on":
            do(["enable", "--now", "owrx-satsched"])
            msg = "Weather satellite auto-capture enabled."
        elif action == "sat_off":
            do(["disable", "--now", "owrx-satsched"])
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
    srv = Server(("0.0.0.0", PORT), Handler)
    print("openwebrxplus SDR control panel on :{}".format(PORT), flush=True)
    srv.serve_forever()
