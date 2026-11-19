#!/usr/bin/env bash
# ==============================================================
#  evilportal — hedef ağ keşfi
#  Çıktıdaki BSSID / CH / ESSID değerlerini config.sh'e yaz.
#  Kullanım: sudo ./scan.sh [iface]
# ==============================================================
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IF="${1:-wlan0}"

[[ $EUID -eq 0 ]] || { echo "root gerekli"; exit 1; }
command -v airodump-ng >/dev/null || { echo "airodump-ng yok"; exit 1; }

airmon-ng check kill >/dev/null 2>&1 || true
ip link set "$IF" down 2>/dev/null
iw dev "$IF" set type monitor 2>/dev/null
ip link set "$IF" up
iw dev "$IF" set power_save off 2>/dev/null

exec airodump-ng --band abg "$IF"
