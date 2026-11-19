#!/usr/bin/env bash
# ==============================================================
#  evilportal — ortak yardımcılar
#  setup.sh / autopwn.sh / scan.sh / deauth.sh tarafından source edilir.
#  Doğrudan çalıştırılmaz.
# ==============================================================

# NetworkManager öldüyse geri getir (airmon-ng check kill onu öldürür).
ep_nm_restart() {
  command -v systemctl >/dev/null 2>&1 || return 0
  systemctl is-active --quiet NetworkManager 2>/dev/null && return 0
  systemctl restart NetworkManager >/dev/null 2>&1 || true
}

# Uplink geri gelene kadar bekle (varsayılan: 15 sn).
ep_wait_uplink() {
  local i n="${1:-15}"
  for ((i = 1; i <= n; i++)); do
    ip -4 route show default 2>/dev/null | grep -q 'dev ' && return 0
    sleep 1
  done
  return 1
}

# Arayüzü monitor moduna al. Başarılıysa monitor arayüz adını stdout'a yazar.
# Önce 'iw' ile dener, olmazsa airmon-ng'ye düşer (wlanXmon oluşturabilir).
ep_set_monitor() {
  local iface="${1:-}" mon new=""

  [[ -n "$iface" ]] || return 1
  iw dev "$iface" info >/dev/null 2>&1 || return 1

  ip link set "$iface" down 2>/dev/null || true
  iw dev "$iface" set type monitor >/dev/null 2>&1 || true
  ip link set "$iface" up 2>/dev/null || true

  if iw dev "$iface" info 2>/dev/null | grep -q 'type monitor'; then
    iw dev "$iface" set power_save off >/dev/null 2>&1 || true
    printf '%s\n' "$iface"
    return 0
  fi

  command -v airmon-ng >/dev/null 2>&1 || return 1

  new=$(airmon-ng start "$iface" 2>/dev/null \
    | sed -e 's/\x1b\[[0-9;]*m//g' \
    | sed -n 's/.*monitor mode enabled on \([^ ]*\).*/\1/p' \
    | head -n1)

  if [[ -z "$new" ]] && iw dev "${iface}mon" info >/dev/null 2>&1; then
    new="${iface}mon"
  fi
  [[ -n "$new" ]] || return 1
  iw dev "$new" info >/dev/null 2>&1 || return 1

  iw dev "$new" set power_save off >/dev/null 2>&1 || true
  printf '%s\n' "$new"
}

# Monitor arayüzünü normale döndür. Sanal arayüzse siler.
ep_restore_iface() {
  local iface="${1:-}" type=""

  [[ -n "$iface" ]] || return 0
  iw dev "$iface" info >/dev/null 2>&1 || return 0

  type=$(iw dev "$iface" info 2>/dev/null \
    | sed -n 's/^[[:space:]]*type \([A-Za-z]*\).*/\1/p' | head -n1)
  [[ "$type" == "monitor" ]] || return 0

  ip link set "$iface" down 2>/dev/null || true
  if iw dev "$iface" set type managed >/dev/null 2>&1; then
    ip link set "$iface" up 2>/dev/null || true
  else
    iw dev "$iface" del >/dev/null 2>&1 || true
  fi
  return 0
}

# airmon-ng'nin oluşturduğu gibi geçici bir arayüzü sil.
ep_drop_vif() {
  local iface="${1:-}"
  [[ -n "$iface" ]] || return 0
  iw dev "$iface" info >/dev/null 2>&1 || return 0
  iw dev "$iface" del >/dev/null 2>&1 || true
  return 0
}
