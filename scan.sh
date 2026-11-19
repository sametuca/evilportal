#!/usr/bin/env bash
# ==============================================================
#  evilportal — hedef ağ keşfi
#  Çıktıdaki BSSID / CH / ESSID değerlerini config.sh'e yaz.
#
#  Kullanım:
#    sudo ./scan.sh [iface] [süre_sn]
#      süre verilmezse Ctrl+C'ye kadar tarar.
#  Çıkışta arayüz managed moda döner, NetworkManager geri açılır.
# ==============================================================
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"
source "$BASE/lib.sh"

IF="${1:-$MON_IF}"
DUR="${2:-0}"

[[ $EUID -eq 0 ]] || { echo "root gerekli"; exit 1; }
command -v airodump-ng >/dev/null || { echo "airodump-ng yok"; exit 1; }
iw dev "$IF" info >/dev/null 2>&1 || { echo "arayüz yok: $IF"; exit 1; }
[[ "$DUR" =~ ^[0-9]+$ ]] || { echo "geçersiz süre: $DUR"; exit 1; }

airmon-ng check kill >/dev/null 2>&1 || true

if ! MON=$(ep_set_monitor "$IF"); then
  echo "[-] $IF monitor mode'a alınamadı"
  exit 1
fi

restore() {
  if [[ "$MON" != "$IF" ]]; then
    ep_drop_vif "$MON"
  else
    ep_restore_iface "$MON"
  fi
  ep_restore_iface "$IF"
  ep_nm_restart
}
trap restore EXIT INT TERM

echo "[*] tarama: $MON (Ctrl+C ile bitir)"
if (( DUR > 0 )); then
  timeout "$DUR" airodump-ng --band abg "$MON" || true
else
  airodump-ng --band abg "$MON" || true
fi
