# TP-Link EX20v v2 — OpenWrt Aracı (macOS)

TP-Link EX20v v2 için stok firmware **tam yedeği alma**, **OpenWrt kurma**,
**açılış slotu yönetme** ve cihazın kendi yedeğinden **stok firmware'e geri dönme**
işlemlerini tek bir araçtan yürütür. Bu, Windows için yazılmış
`ex20v-tool.ps1` aracının **macOS portudur**.

> ⚠️ **Flash belleğe yazan bir araçtır. Yanlış kullanım cihazı brick'leyebilir.**
> Tüm sorumluluk kullanıcıya aittir. İlk iş olarak **1) Tam yedek** alın.

---

## Atıf / Credits

Bu macOS portu, aşağıdaki özgün çalışmaya dayanır ve onun mantığını birebir korur:

- **Özgün araç (Windows/PowerShell):**
  https://github.com/yucellmustafa/tplink_ex20v_openwrt_tool — *yucellmustafa*
- **OpenWrt projesi** ve EX20v v2 desteğine katkıda bulunan herkes.

Cihaz üzerinde çalışan POSIX shell ajanı (`agent.sh`) **özgün araçtan bire bir**
alınmıştır (`sha256: 465ad533bee82c43c89c5b7f4b42bd580d844d8a269aa7425c12bcbad0f72be6`).
Yalnızca bilgisayar (host) tarafı — dosya sunucusu ve SSH oturum yönetimi —
macOS için Python'da yeniden yazılmıştır.

Bu bağımsız bir topluluk çalışmasıdır; TP-Link, Türk Telekom, EcoNet, MediaTek
veya OpenWrt ile hiçbir bağlantısı yoktur.

---

## Standalone — kurulum gerektirmez

`ex20v-tool` tek parça bir macOS çalıştırılabilir dosyasıdır (PyInstaller ile
derlenmiştir). Çalıştırmak için **Python, Homebrew veya başka hiçbir şey
kurmanıza gerek yoktur.** macOS'un kendi `ssh` / `tftp` araçlarını kullanır.

İmzasız olduğu için ilk açılışta macOS Gatekeeper engelleyebilir:

```sh
# karantina etiketini kaldır (klasörün içinde)
xattr -dr com.apple.quarantine ex20v-tool
./ex20v-tool
```

Alternatif: Finder'da dosyaya **sağ tık → Aç** deyip onaylayın.

İlk kez ağ sunucusu açtığında macOS güvenlik duvarı "gelen bağlantılara izin
verilsin mi?" diye sorabilir → **İzin Ver**.

**Binary sha256:** `41e02481324079cad25bdbc3d02f169cbff983a872152a10bd6460fefbefa3eb`
(kendi derlemenizde farklı olur; `dist/ex20v-tool.sha256` dosyasına bakın.)

---

## Klasör düzeni

```
ex20v-tool-macos/
├── ex20v-tool          ← çalıştırılabilir (bunu çalıştırın)
├── files/              ← firmware imajları (araçla aynı klasörde olmalı)
│   ├── ex20v-rescue.bin
│   ├── ex20v-kernel.bin
│   ├── ex20v-ubi.bin
│   └── ex20v-v2-sysupgrade.bin
└── Backups/            ← alınan yedekler buraya kaydedilir (otomatik oluşur)
```

---

## Kullanım

```sh
./ex20v-tool            # port 8000 (varsayılan)
./ex20v-tool 8080       # farklı port
```

Menü:

1. **Stok firmware tam yedeği al** — flash'a yazmaz, sadece okur (~450 MB).
   *İlk iş bu olmalı.*
2. **OpenWrt kurulumu** (iki adım)
   - 2A: Stok sistemde rescue OpenWrt'yi kur
   - 2B: Rescue ile açılınca ana OpenWrt'yi kur
3. **Stok firmware geri yükleme** (iki adım) — cihazın kendi yedeğini kullanır
4. **Kurtarma ve açılış slotu** — bootflag göster / değiştir
5. **Dosyaları ve gereksinimleri kontrol et** — salt okunur kontrol

İlk kurulumdan önce stok firmware üzerinde **root SSH erişimi** açılmış olmalıdır.

### İlk güvenli test

Cihaza hiçbir şey yazmadan önce **5)** ile dosyaları doğrulayın, sonra
**4 → 1) Durumu göster** ile salt-okunur SSH bağlantısını test edin. Bunlar
flash'a dokunmaz.

---

## Kaynaktan derleme

```sh
sh build.sh          # .venv kurar, pexpect+pyinstaller yükler, dist/ex20v-tool üretir
```

Çıktı, derlendiği makinenin mimarisine (Apple Silicon arm64 veya Intel x86_64)
göre üretilir. Her iki mimari için ayrı ayrı derlemeniz gerekir.

Gereksinimler (yalnızca **derlerken**): Python 3.9+, `pip`.

---

## Windows ↔ macOS farkları

| Katman | Windows (`.ps1`) | macOS (bu port) |
|---|---|---|
| Cihaz ajanı | `agent.sh` (gömülü) | **aynısı** (`agent.sh`, gömülü) |
| SSH istemcisi | Windows OpenSSH | macOS `ssh` |
| Dosya indirme/yükleme | .NET HTTP/TFTP sunucu | Python HTTP/TFTP sunucu |
| SSH oturumu | .NET Process + stdin durum makinesi | `pexpect` PTY durum makinesi |
| Ajan aktarımı | octal `printf` parçalı | **aynı yöntem** + boyut doğrulaması |

Aktarılan ajanın `/tmp/x` boyutu, çalıştırmadan önce beklenen boyutla
karşılaştırılır (fail-closed); uyuşmazsa hiçbir işlem başlatılmaz.

---

## Lisans

Özgün depo açık bir lisans dosyası içermez. Bu port, özgün yazara atıf
korunarak ve onunla koordineli olarak yayımlanmıştır. Firmware imajları
(`files/`) özgün çalışmanın ürünüdür.
