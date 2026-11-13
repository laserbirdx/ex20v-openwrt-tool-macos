#!/bin/sh
set -u

ACTION="${1:-}"
PC="${2:-}"
PORT="${3:-8000}"
BS=262144

say() { printf '%s\n' "$*"; }
die() { say "HATA: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "'$1' komutu bulunamadi"; }
mtdn() { sed -n "s/^\(mtd[0-9][0-9]*\):.*\"$1\"$/\1/p" /proc/mtd | head -n 1; }
root_dev() { awk '$5=="/" {print $3; exit}' /proc/self/mountinfo; }
root_fs() { awk '$2=="/" {print $3; exit}' /proc/mounts; }

[ -r /proc/mtd ] || die 'Linux shell acilamadi (/proc/mtd yok)'
say 'EX20V_SHELL=LINUX'

require_ex20v_layout() {
    [ -r /proc/mtd ] || die '/proc/mtd okunamiyor'
    grep -q '^mtd4: 00600000 .* "kernel"$' /proc/mtd || die 'slot A kernel bolumu beklenen duzende degil'
    grep -q '^mtd7: 00600000 .* "kernel_2"$' /proc/mtd || die 'slot B kernel bolumu beklenen duzende degil'
    grep -q '^mtd8: 02000000 .* "rootfs_2"$' /proc/mtd || die 'slot B rootfs bolumu beklenen duzende degil'
    grep -q '^mtd10: 17080000 .* "reserve_area"$' /proc/mtd || die 'reserve_area beklenen duzende degil'
}

require_known_device() {
    if is_openwrt; then
        [ -n "$(mtdn bootflag)" ] || die 'OpenWrt bootflag bolumu bulunamadi'
        [ -n "$(mtdn kernel)" ] || die 'OpenWrt kernel bolumu bulunamadi'
        [ -n "$(mtdn ubi)" ] || die 'OpenWrt UBI bolumu bulunamadi'
    else
        require_ex20v_layout
    fi
}

is_openwrt() {
    grep -q 'tplink,ex20v-v2' /tmp/sysinfo/board_name 2>/dev/null
}

environment() {
    if is_openwrt; then
        case "$(root_fs)" in
            rootfs|tmpfs|ramfs) printf rescue ;;
            *)                  printf openwrt ;;
        esac
    elif require_ex20v_layout >/dev/null 2>&1; then
        printf stock
    else
        printf unknown
    fi
}

fetch() {
    out="$1"; name="$2"
    rm -f "$out"
    if command -v curl >/dev/null 2>&1; then
        curl -sS -f -o "$out" "http://$PC:$PORT/$name" || return 1
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$out" "http://$PC:$PORT/$name" || return 1
    else
        return 1
    fi
}

select_restore_dir() {
    required_kib="$1"
    for base in /tmp /var/tmp/dev; do
        [ -d "$base" ] || continue
        available="$(df -Pk "$base" 2>/dev/null | awk 'END {print $4}')"
        case "$available" in ''|*[!0-9]*) continue ;; esac
        [ "$available" -ge "$required_kib" ] || continue
        work="$base/ex20v-restore"
        mkdir -p "$work" 2>/dev/null || continue
        probe="$work/.write-test.$$"
        if (umask 077; : > "$probe") 2>/dev/null; then
            rm -f "$probe"
            printf '%s' "$work"
            return 0
        fi
        rmdir "$work" 2>/dev/null || true
    done
    return 1
}

upload() {
    file="$1"; name="${2:-$(basename "$1")}" 
    if command -v curl >/dev/null 2>&1; then
        curl -sS -f -T "$file" "http://$PC:$PORT/$name" >/dev/null || return 1
    elif command -v tftp >/dev/null 2>&1; then
        tftp -p -b 1428 -l "$file" -r "$name" "$PC" "$PORT" >/dev/null 2>&1 ||
            tftp -p -l "$file" -r "$name" "$PC" "$PORT" >/dev/null 2>&1 || return 1
    else
        return 1
    fi
    say "gonderildi: $name"
}

make_char_device() {
    number="$1"; path="$2"
    if [ -e "/dev/mtd$number" ]; then
        printf '/dev/mtd%s' "$number"
        return 0
    fi
    dev="$(cat "/sys/class/mtd/mtd$number/dev" 2>/dev/null)"
    [ -n "$dev" ] || return 1
    rm -f "$path"
    mknod "$path" c "${dev%%:*}" "${dev##*:}" || return 1
    printf '%s' "$path"
}

stock_flag() {
    dd if=/dev/mtdblock2 bs=$BS skip=4 count=1 2>/dev/null | head -c 1
}

openwrt_flag() {
    fp="$(mtdn bootflag)"
    [ -n "$fp" ] || return 1
    dd if="/dev/$fp" bs=1 count=1 2>/dev/null
}

show_slot() {
    env="$(environment)"
    if [ "$env" = stock ]; then flag="$(stock_flag)"; else flag="$(openwrt_flag)"; fi
    case "$env" in
        stock)   env_desc='Stok firmware' ;;
        rescue)  env_desc='Rescue OpenWrt' ;;
        openwrt) env_desc='Ana OpenWrt' ;;
        *)       env_desc='Bilinmeyen sistem' ;;
    esac
    case "$flag" in
        0) desc='Slot A / rescue' ;;
        1) desc='Slot B / ana sistem' ;;
        *) desc='GECERSIZ' ;;
    esac
    say "Calisan sistem : $env_desc"
    say "Acilis tercihi : $desc (bootflag=$flag)"
    say "Root bolumu    : $(root_dev) / $(root_fs)"
}

set_stock_flag() {
    value="$1"; old=/tmp/ex20v-flag-old.bin; new=/tmp/ex20v-flag-new.bin
    dd if=/dev/mtdblock2 of="$old" bs=$BS skip=4 count=1 2>/dev/null || die 'bootflag blogu okunamadi'
    [ "$(wc -c < "$old")" -eq "$BS" ] || die 'bootflag blogu eksik okundu'
    printf '%s' "$value" > "$new"
    dd if="$old" bs=1 skip=1 2>/dev/null >> "$new" || die 'bootflag blogu hazirlanamadi'
    dd if="$new" of=/dev/mtdblock2 bs=$BS seek=4 count=1 conv=notrunc 2>/dev/null || die 'bootflag yazilamadi'
    sync
    echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
    a="$(md5sum < "$new")"; b="$(dd if=/dev/mtdblock2 bs=$BS skip=4 count=1 2>/dev/null | md5sum)"
    rm -f "$old" "$new"
    [ "$a" = "$b" ] || die 'bootflag dogrulanamadi; yeniden baslatmayin'
}

set_openwrt_flag() {
    value="$1"; fp="$(mtdn bootflag)"; out=/tmp/ex20v-bootflag.bin
    [ -n "$fp" ] || die 'bootflag bolumu bulunamadi'
    printf '%s' "$value" > "$out"
    dd if=/dev/zero bs=$((BS-1)) count=1 2>/dev/null | tr '\000' '\377' >> "$out"
    mtd write "$out" bootflag >/dev/null || die 'bootflag yazilamadi'
    actual="$(dd if="/dev/$fp" bs=1 count=1 2>/dev/null)"
    rm -f "$out"
    [ "$actual" = "$value" ] || die "bootflag dogrulanamadi (okunan: '$actual')"
}

set_slot() {
    value="$1"
    case "$value" in 0|1) ;; *) die 'slot yalnizca 0 veya 1 olabilir' ;; esac
    if [ "$(environment)" = stock ]; then set_stock_flag "$value"; else set_openwrt_flag "$value"; fi
    say "bootflag='$value' DOGRULANDI"
}

backup_all() {
    [ -n "$PC" ] || die 'PC IP adresi verilmedi'
    require_ex20v_layout
    [ "$(environment)" = stock ] || die 'tam yedek yalnizca stok firmware uzerinde alinabilir'
    command -v curl >/dev/null 2>&1 || command -v tftp >/dev/null 2>&1 || die 'curl veya tftp komutu bulunamadi'
    need dd; need md5sum
    cd /tmp || die '/tmp acilamadi'

    say 'Sistem bilgileri toplaniyor...'
    {
        echo '### date';         date
        echo '### version';      cat /proc/version
        echo '### cpuinfo';      cat /proc/cpuinfo
        echo '### meminfo';      cat /proc/meminfo
        echo '### mtd';          cat /proc/mtd
        echo '### partitions';   cat /proc/partitions
        echo '### devices';      ls -l /dev/mtd* /dev/ubi* 2>&1
        echo '### mounts';       cat /proc/mounts
        echo '### df';           df
        echo '### ubinfo';       ubinfo -a 2>&1
        echo '### mtd sysfs'
        for d in /sys/class/mtd/mtd*; do
            echo "$d name=$(cat "$d/name" 2>/dev/null) dev=$(cat "$d/dev" 2>/dev/null) size=$(cat "$d/size" 2>/dev/null) erase=$(cat "$d/erasesize" 2>/dev/null) write=$(cat "$d/writesize" 2>/dev/null)"
        done
        echo '### interfaces';   ifconfig -a 2>&1
    } > ex20v-info.txt 2>&1
    upload ex20v-info.txt info.txt || die 'info.txt gonderilemedi'
    rm -f ex20v-info.txt

    : > ex20v-md5.txt
    for n in 0 1 2 3 4 5 7 8; do
        src="$(make_char_device "$n" "/tmp/ex20v-mtd$n")" || die "mtd$n cihaz dosyasi olusturulamadi"
        name="$(tr -d '\n' < "/sys/class/mtd/mtd$n/name" 2>/dev/null | tr -c 'A-Za-z0-9._-' '_')"
        file="mtd${n}_${name}.bin"
        say "okunuyor: mtd$n -> $file"
        dd if="$src" of="$file" bs=4096 2>/tmp/ex20v-dd-error || die "mtd$n okunamadi: $(cat /tmp/ex20v-dd-error)"
        md5sum "$file" >> ex20v-md5.txt
        upload "$file" "$file" || die "$file gonderilemedi"
        rm -f "$file" "/tmp/ex20v-mtd$n"
    done
    upload ex20v-md5.txt md5.txt || die 'md5.txt gonderilemedi'
    rm -f ex20v-md5.txt /tmp/ex20v-dd-error

    say 'reserve_area yedegi aliniyor (yaklasik 368 MiB)...'
    src="$(make_char_device 10 /tmp/ex20v-mtd10)" || die 'mtd10 cihaz dosyasi olusturulamadi'
    : > ex20v-reserve-md5.txt
    i=0; part=0; total=1474; chunk=128
    while [ "$i" -lt "$total" ]; do
        count=$chunk
        [ $((i+count)) -gt "$total" ] && count=$((total-i))
        file="$(printf 'reserve_%02d.bin' "$part")"
        say "okunuyor: $file (blok $i, $count blok)"
        dd if="$src" of="$file" bs=$BS skip="$i" count="$count" 2>/dev/null || die "$file okunamadi"
        [ "$(wc -c < "$file")" -eq $((count*BS)) ] || die "$file eksik okundu"
        md5sum "$file" >> ex20v-reserve-md5.txt
        upload "$file" "$file" || die "$file gonderilemedi"
        rm -f "$file"
        i=$((i+count)); part=$((part+1))
    done
    upload ex20v-reserve-md5.txt reserve_md5.txt || die 'reserve_md5.txt gonderilemedi'
    rm -f ex20v-reserve-md5.txt /tmp/ex20v-mtd10
}

prepare_rescue() {
    [ -n "$PC" ] || die 'PC IP adresi verilmedi'
    require_ex20v_layout
    [ "$(environment)" = stock ] || die 'bu adim stok firmware uzerinde calistirilmalidir'
    [ "$(root_dev)" = '31:8' ] || die 'stok sistem slot B uzerinden calismiyor; once menuden slot B secilip cihaz yeniden baslatilmali'
    [ "$(stock_flag)" = '1' ] || die 'bootflag slot B (1) degil'

    cd /tmp || die '/tmp acilamadi'
    image=ex20v-rescue.bin
    fetch "$image" "$image" || die "$image PCden indirilemedi"
    size="$(wc -c < "$image")"
    [ "$size" -gt 1048576 ] && [ "$size" -le 6291456 ] || die 'rescue imaji boyutu gecersiz'
    magic="$(head -c 4 "$image" | hexdump -v -e '4/1 "%02x"')"
    entry="$(dd if="$image" bs=1 skip=108 count=4 2>/dev/null | hexdump -v -e '4/1 "%02x"')"
    [ "$magic" = 03000003 ] || die 'rescue imajinda TP-Link basligi yok'
    [ "$entry" = 00200080 ] || die 'rescue imaji giris adresi beklenmedik'

    pad=$(( (BS-size%BS)%BS ))
    [ "$pad" -eq 0 ] || dd if=/dev/zero bs="$pad" count=1 2>/dev/null | tr '\000' '\377' >> "$image"
    blocks=$(( $(wc -c < "$image")/BS ))
    say "rescue slot A'ya yaziliyor ($blocks blok)..."
    dd if="$image" of=/dev/mtdblock4 bs=$BS count="$blocks" conv=notrunc 2>/dev/null || die 'slot A yazilamadi'
    sync
    echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
    expected="$(md5sum < "$image" | cut -c1-32)"
    actual="$(dd if=/dev/mtd4 bs=$BS count="$blocks" 2>/dev/null | md5sum | cut -c1-32)"
    [ "$expected" = "$actual" ] || die 'rescue yazma dogrulamasi basarisiz; yeniden baslatmayin'
    say 'rescue imaji DOGRULANDI'
    rm -f "$image"

    set_stock_flag 0
    say "bootflag='0' DOGRULANDI"
}

install_main() {
    [ -n "$PC" ] || die 'PC IP adresi verilmedi'
    is_openwrt || die 'cihaz EX20v v2 OpenWrt olarak taninmadi'
    case "$(root_fs)" in rootfs|tmpfs|ramfs) ;; *) die 'rescue (initramfs) sistemi calismiyor' ;; esac
    for tool in ubiformat ubiattach ubidetach mtd; do need "$tool"; done

    kp="$(mtdn kernel)"; up="$(mtdn ubi)"; fp="$(mtdn bootflag)"
    [ -n "$kp" ] && [ -n "$up" ] && [ -n "$fp" ] || die 'kernel/ubi/bootflag bolumleri bulunamadi'
    say "kernel=$kp ubi=$up bootflag=$fp"
    cd /tmp || die '/tmp acilamadi'
    rm -f ex20v-md5.txt ex20v-kernel.bin ex20v-ubi.bin

    fetch ex20v-md5.txt md5.txt || die 'yedegin md5.txt dosyasi indirilemedi'
    stock_k="$(grep 'mtd7_kernel_2.bin' ex20v-md5.txt | cut -c1-32)"
    stock_r="$(grep 'mtd8_rootfs_2.bin' ex20v-md5.txt | cut -c1-32)"
    [ "${#stock_k}" -eq 32 ] && [ "${#stock_r}" -eq 32 ] || die 'md5.txt icinde mtd7/mtd8 kayitlari yok'

    say '0/3 NAND bad-block esleme kontrolu...'
    current_k="$(dd if="/dev/$kp" bs=$BS count=24 2>/dev/null | md5sum | cut -c1-32)"
    entry="$(dd if="/dev/$kp" bs=1 skip=108 count=4 2>/dev/null | hexdump -v -e '4/1 "%02x"')"
    if [ "$current_k" = "$stock_k" ]; then
        current_r="$(dd if="/dev/$up" bs=$BS count=128 2>/dev/null | md5sum | cut -c1-32)"
        [ "$current_r" = "$stock_r" ] || die 'slot B kernel uyumlu fakat rootfs_2 yedekle uyusmuyor; hicbir sey yazilmadi'
        say 'slot B stok yedekle uyumlu'
    elif [ "$entry" = 00200080 ]; then
        say 'slot B zaten OpenWrt iceriyor; yeniden kurulum devam ediyor'
    else
        die 'slot B ham okuma cihaz yedegiyle uyusmuyor; bad-block kaymasi olabilir'
    fi

    fetch ex20v-kernel.bin ex20v-kernel.bin || die 'ex20v-kernel.bin indirilemedi'
    fetch ex20v-ubi.bin ex20v-ubi.bin || die 'ex20v-ubi.bin indirilemedi'
    kernel_size="$(wc -c < ex20v-kernel.bin)"; ubi_size="$(wc -c < ex20v-ubi.bin)"
    [ "$kernel_size" -gt 1048576 ] && [ "$kernel_size" -le 6291456 ] || die 'kernel boyutu gecersiz'
    [ "$ubi_size" -gt 1048576 ] || die 'UBI imaji boyutu gecersiz'
    [ "$(head -c 4 ex20v-kernel.bin | hexdump -v -e '4/1 "%02x"')" = 03000003 ] || die 'kernel TP-Link basligi gecersiz'
    [ "$(head -c 4 ex20v-ubi.bin)" = 'UBI#' ] || die 'rootfs dosyasi UBI imaji degil'

    say "1/3 kernel slot B'ye yaziliyor..."
    mtd write ex20v-kernel.bin kernel || die 'kernel yazilamadi; bootflag degistirilmedi'
    expected="$(md5sum < ex20v-kernel.bin | cut -c1-32)"
    actual="$(dd if="/dev/$kp" bs="$kernel_size" count=1 2>/dev/null | md5sum | cut -c1-32)"
    [ "$expected" = "$actual" ] || die 'kernel dogrulanamadi; bootflag degistirilmedi'
    say 'kernel DOGRULANDI'

    say '2/3 UBI rootfs yaziliyor...'
    ubidetach -p "/dev/$up" >/dev/null 2>&1 || true
    ubiformat "/dev/$up" -y -f ex20v-ubi.bin || die 'UBI yazilamadi; bootflag degistirilmedi'
    if ubiattach -p "/dev/$up" >/dev/null 2>&1; then
        ubinfo -a | grep -E 'Name|Size' || true
        ubidetach -p "/dev/$up" >/dev/null 2>&1 || true
    fi
    say 'UBI YAZILDI'

    say "3/3 bootflag slot B'ye aliniyor..."
    set_openwrt_flag 1
    rm -f ex20v-md5.txt ex20v-kernel.bin ex20v-ubi.bin
    say "bootflag='1' DOGRULANDI"
}

backup_hash() {
    manifest="$1"; name="$2"
    awk -v n="$name" '$2 == n || $2 == "*" n { print $1; exit }' "$manifest"
}

fetch_checked() {
    name="$1"; manifest="$2"; out="$RESTORE_DIR/$name"
    expected="$(backup_hash "$manifest" "$name")"
    [ "${#expected}" -eq 32 ] || die "$name MD5 kaydi bulunamadi"
    rm -f "$out"
    fetch "$out" "$name" || die "$name indirilemedi"
    actual="$(md5sum < "$out" | cut -c1-32)"
    [ "$actual" = "$expected" ] || die "$name indirme dogrulamasi basarisiz"
}

require_same_backup_device() {
    manifest="$1"
    rom="$(mtdn romfile)"; radio="$(mtdn radio)"
    [ -n "$rom" ] && [ -n "$radio" ] || die 'romfile/radio bolumleri bulunamadi'
    expected_rom="$(backup_hash "$manifest" mtd1_romfile.bin)"
    expected_radio="$(backup_hash "$manifest" mtd3_radio.bin)"
    [ "${#expected_rom}" -eq 32 ] && [ "${#expected_radio}" -eq 32 ] || die 'cihaz kimlik MD5 kayitlari eksik'
    actual_rom="$(md5sum < "/dev/$rom" | cut -c1-32)"
    actual_radio="$(md5sum < "/dev/$radio" | cut -c1-32)"
    [ "$actual_rom" = "$expected_rom" ] || die 'yedek baska cihaza ait (romfile uyusmuyor)'
    [ "$actual_radio" = "$expected_radio" ] || die 'yedek baska cihaza ait (radio uyusmuyor)'
    say 'Yedek cihaz kimligi DOGRULANDI'
}

restore_stock() {
    [ -n "$PC" ] || die 'PC IP adresi verilmedi'
    is_openwrt || die 'stok geri yukleme yalnizca rescue OpenWrt uzerinde baslatilabilir'
    case "$(root_fs)" in rootfs|tmpfs|ramfs) ;; *) die 'rescue (initramfs) sistemi calismiyor' ;; esac
    [ "$(openwrt_flag)" = 0 ] || die "bootflag rescue slotunda (0) degil; hicbir sey yazilmadi"
    for tool in mtd ubidetach md5sum; do need "$tool"; done
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || die 'curl veya wget komutu bulunamadi'

    kp="$(mtdn kernel)"; up="$(mtdn ubi)"; fp="$(mtdn bootflag)"
    [ -n "$kp" ] && [ -n "$up" ] && [ -n "$fp" ] || die 'kernel/ubi/bootflag bolumleri bulunamadi'
    [ "$(cat "/sys/class/mtd/$kp/size")" = 6291456 ] || die 'kernel bolumu boyutu beklenmedik'
    [ "$(cat "/sys/class/mtd/$up/size")" = 419954688 ] || die 'ubi bolumu boyutu beklenmedik'
    RESTORE_DIR="$(select_restore_dir 40000)" || die 'geri yukleme icin en az 40 MiB kullanilabilir RAM alani gerekir; hicbir sey yazilmadi'
    say "Gecici RAM alani: $RESTORE_DIR"
    cd "$RESTORE_DIR" || die 'gecici calisma dizini acilamadi'
    rm -f stock-md5.txt reserve-md5.txt mtd7_kernel_2.bin mtd8_rootfs_2.bin reserve_??.bin
    fetch stock-md5.txt md5.txt || die 'md5.txt indirilemedi'
    fetch reserve-md5.txt reserve_md5.txt || die 'reserve_md5.txt indirilemedi'
    require_same_backup_device stock-md5.txt
    [ "$(grep -c 'reserve_[0-9][0-9]\.bin' reserve-md5.txt)" -eq 12 ] || die 'reserve manifesti 12 parca icermiyor'
    ubidetach -p "/dev/$up" >/dev/null 2>&1 || true

    say '1/3 reserve_area geri yukleniyor...'
    i=0
    while [ "$i" -lt 12 ]; do
        name="$(printf 'reserve_%02d.bin' "$i")"
        count=128; [ "$i" -eq 11 ] && count=66
        skip=$((128 + i * 128))
        fetch_checked "$name" reserve-md5.txt
        [ "$(wc -c < "$RESTORE_DIR/$name")" -eq $((count * BS)) ] || die "$name boyutu gecersiz"
        mtd -p $((skip * BS)) write "$RESTORE_DIR/$name" ubi >/dev/null || die "$name yazilamadi; rescue korunuyor"
        expected="$(backup_hash reserve-md5.txt "$name")"
        actual="$(dd if="/dev/$up" bs=$BS skip="$skip" count="$count" 2>/dev/null | md5sum | cut -c1-32)"
        [ "$actual" = "$expected" ] || die "$name yazma dogrulamasi basarisiz; rescue korunuyor"
        rm -f "$RESTORE_DIR/$name"
        say "DOGRULANDI: $name"
        i=$((i + 1))
    done

    say '2/3 stok rootfs_2 geri yukleniyor...'
    fetch_checked mtd8_rootfs_2.bin stock-md5.txt
    [ "$(wc -c < mtd8_rootfs_2.bin)" -eq 33554432 ] || die 'rootfs_2 boyutu gecersiz'
    mtd -p 0 write mtd8_rootfs_2.bin ubi >/dev/null || die 'rootfs_2 yazilamadi; rescue korunuyor'
    expected="$(backup_hash stock-md5.txt mtd8_rootfs_2.bin)"
    actual="$(dd if="/dev/$up" bs=$BS count=128 2>/dev/null | md5sum | cut -c1-32)"
    [ "$actual" = "$expected" ] || die 'rootfs_2 dogrulanamadi; rescue korunuyor'
    rm -f mtd8_rootfs_2.bin
    say 'rootfs_2 DOGRULANDI'

    say '3/3 stok kernel_2 geri yukleniyor...'
    fetch_checked mtd7_kernel_2.bin stock-md5.txt
    [ "$(wc -c < mtd7_kernel_2.bin)" -eq 6291456 ] || die 'kernel_2 boyutu gecersiz'
    mtd write mtd7_kernel_2.bin kernel >/dev/null || die 'kernel_2 yazilamadi; rescue korunuyor'
    expected="$(backup_hash stock-md5.txt mtd7_kernel_2.bin)"
    actual="$(dd if="/dev/$kp" bs=$BS count=24 2>/dev/null | md5sum | cut -c1-32)"
    [ "$actual" = "$expected" ] || die 'kernel_2 dogrulanamadi; rescue korunuyor'
    rm -f mtd7_kernel_2.bin
    sync

    set_openwrt_flag 1
    rm -f stock-md5.txt reserve-md5.txt
    cd /
    rmdir "$RESTORE_DIR" 2>/dev/null || true
    say "bootflag='1' DOGRULANDI; rescue slot A korunuyor"
}

restore_stock_finalize() {
    [ -n "$PC" ] || die 'PC IP adresi verilmedi'
    require_ex20v_layout
    [ "$(environment)" = stock ] || die 'tamamlama yalnizca stok firmware uzerinde yapilabilir'
    [ "$(root_dev)" = '31:8' ] || die 'stok sistem slot B uzerinden calismiyor; hicbir sey yazilmadi'
    [ "$(stock_flag)" = 1 ] || die "bootflag slot B (1) degil; hicbir sey yazilmadi"
    need dd; need md5sum
    RESTORE_DIR="$(select_restore_dir 42000)" || die 'geri yukleme icin en az 42 MiB kullanilabilir RAM alani gerekir; hicbir sey yazilmadi'
    say "Gecici RAM alani: $RESTORE_DIR"
    cd "$RESTORE_DIR" || die 'gecici calisma dizini acilamadi'
    rm -f stock-md5.txt mtd4_kernel.bin mtd5_rootfs.bin
    fetch stock-md5.txt md5.txt || die 'md5.txt indirilemedi'
    require_same_backup_device stock-md5.txt
    fetch_checked mtd5_rootfs.bin stock-md5.txt
    fetch_checked mtd4_kernel.bin stock-md5.txt
    [ "$(wc -c < mtd5_rootfs.bin)" -eq 33554432 ] || die 'slot A rootfs boyutu gecersiz'
    [ "$(wc -c < mtd4_kernel.bin)" -eq 6291456 ] || die 'slot A kernel boyutu gecersiz'

    say '1/2 stok slot A rootfs geri yukleniyor...'
    dd if=mtd5_rootfs.bin of=/dev/mtdblock5 bs=$BS count=128 conv=notrunc 2>/dev/null || die 'slot A rootfs yazilamadi'
    expected="$(backup_hash stock-md5.txt mtd5_rootfs.bin)"
    actual="$(dd if=/dev/mtd5 bs=$BS count=128 2>/dev/null | md5sum | cut -c1-32)"
    [ "$actual" = "$expected" ] || die 'slot A rootfs dogrulanamadi'
    rm -f mtd5_rootfs.bin
    say 'slot A rootfs DOGRULANDI'

    say '2/2 stok slot A kernel geri yukleniyor...'
    dd if=mtd4_kernel.bin of=/dev/mtdblock4 bs=$BS count=24 conv=notrunc 2>/dev/null || die 'slot A kernel yazilamadi'
    expected="$(backup_hash stock-md5.txt mtd4_kernel.bin)"
    actual="$(dd if=/dev/mtd4 bs=$BS count=24 2>/dev/null | md5sum | cut -c1-32)"
    [ "$actual" = "$expected" ] || die 'slot A kernel dogrulanamadi'
    sync
    rm -f stock-md5.txt mtd4_kernel.bin mtd5_rootfs.bin
    cd /
    rmdir "$RESTORE_DIR" 2>/dev/null || true
    say 'slot A kernel DOGRULANDI'
}

case "$ACTION" in
    inspect)
        require_known_device
        show_slot
        ;;
    backup)
        backup_all
        ;;
    prepare-rescue)
        prepare_rescue
        ;;
    install-main)
        install_main
        ;;
    restore-stock)
        restore_stock
        ;;
    restore-stock-finalize)
        restore_stock_finalize
        ;;
    slot-show)
        require_known_device
        show_slot
        ;;
    slot-set)
        require_known_device
        set_slot "${4:-}"
        show_slot
        ;;
    reboot)
        say 'Cihaz 3 saniye icinde yeniden baslatilacak.'
        (sleep 3; /sbin/reboot) >/dev/null 2>&1 &
        ;;
    *)
        die "bilinmeyen islem: $ACTION"
        ;;
esac
