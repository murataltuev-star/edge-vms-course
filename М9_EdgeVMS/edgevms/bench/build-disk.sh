#!/usr/bin/env bash
# Lesson 1, Steps 4–5 — build the A/B bench disk.
#
#   ESP | rootfs0 (A) | rootfs1 (B) | data
#
# Both slots leave here byte-identical except /etc/slot-id, exactly as the
# factory would ship them. Nothing device-specific goes into a slot.
# Run on a Linux host with: qemu-utils parted e2fsprogs dosfstools debootstrap
set -euo pipefail

BENCH="${BENCH:-$HOME/edge-bench}"
DISK="$BENCH/appliance.qcow2"
SIZE="${SIZE:-40G}"
NBD="${NBD:-/dev/nbd0}"
SUITE="${SUITE:-bookworm}"
MIRROR="${MIRROR:-http://deb.debian.org/debian}"
ROOT_PASSWORD="${ROOT_PASSWORD:-}"        # empty: you will be prompted by passwd
HERE="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$BENCH"
if [ -e "$DISK" ]; then
    echo "$DISK exists; delete it to rebuild" >&2; exit 1
fi
qemu-img create -f qcow2 "$DISK" "$SIZE"

sudo modprobe nbd max_part=8
sudo qemu-nbd --connect="$NBD" "$DISK"
trap 'set +e; for d in dev proc sys; do sudo umount /mnt/slotA/$d 2>/dev/null; done;
      sudo umount /mnt/slotA/boot/efi /mnt/slotA /mnt/slotB 2>/dev/null; sudo qemu-nbd --disconnect "$NBD" >/dev/null' EXIT
sleep 1

# --- partitions (Lesson 1, Step 3) -------------------------------------
sudo parted -s "$NBD" mklabel gpt
sudo parted -s "$NBD" mkpart ESP     fat32  1MiB     513MiB
sudo parted -s "$NBD" set 1 esp on
sudo parted -s "$NBD" mkpart rootfs0 ext4   513MiB   8705MiB
sudo parted -s "$NBD" mkpart rootfs1 ext4   8705MiB  16897MiB
sudo parted -s "$NBD" mkpart data    ext4   16897MiB 100%
sudo parted -s "$NBD" print

# Labels are identity; device names are discovery order.
sudo mkfs.vfat -F32 -n ESP    "${NBD}p1"
sudo mkfs.ext4 -q  -L rootfs0 "${NBD}p2"
sudo mkfs.ext4 -q  -L rootfs1 "${NBD}p3"
sudo mkfs.ext4 -q  -L data    "${NBD}p4"
lsblk -f "$NBD" | tee "$BENCH/healthy-lsblk.txt"     # what healthy looked like (Lesson 3 needs it)

# --- slot A: a root filesystem built as a directory tree ----------------
sudo mkdir -p /mnt/slotA /mnt/slotB
sudo mount "${NBD}p2" /mnt/slotA
sudo debootstrap --arch=amd64 \
    --include=linux-image-amd64,grub-efi-amd64,grub-common,systemd-sysv,rauc,podman,curl,ca-certificates,python3 \
    "$SUITE" /mnt/slotA "$MIRROR"

if [ -n "$ROOT_PASSWORD" ]; then
    echo "root:$ROOT_PASSWORD" | sudo chroot /mnt/slotA chpasswd
else
    sudo chroot /mnt/slotA passwd root
fi
echo 'slot A' | sudo tee /mnt/slotA/etc/slot-id >/dev/null

# Everything that must be IN THE IMAGE (Lesson 4: "the moment you type it by
# hand is the moment it is missing from slot B"):
sudo install -D -m 0644 "$HERE/rauc/system.conf"            /mnt/slotA/etc/rauc/system.conf
sudo install -D -m 0644 "$HERE/quadlet/storage.conf"        /mnt/slotA/etc/containers/storage.conf
sudo install -D -m 0644 "$HERE/quadlet/vms-agent.container" /mnt/slotA/etc/containers/systemd/vms-agent.container
sudo install -D -m 0755 "$HERE/health/rauc-health-check"    /mnt/slotA/usr/local/bin/rauc-health-check
sudo install -D -m 0644 "$HERE/health/rauc-mark-good.service" /mnt/slotA/etc/systemd/system/rauc-mark-good.service
sudo chroot /mnt/slotA systemctl enable rauc-mark-good.service     # a plain unit: enable is right here
# The keyring is a trust anchor and belongs in the image too — but only after
# pki/make-ca.sh has run. Lesson 2 copies it in by hand the first time.
[ -f "$HERE/pki/out/keyring.pem" ] && sudo install -D -m 0644 "$HERE/pki/out/keyring.pem" /mnt/slotA/etc/rauc/keyring.pem

# fstab: the slots are ro; /data is the only writable partition
sudo tee /mnt/slotA/etc/fstab >/dev/null <<'FSTAB'
LABEL=data   /data       ext4  defaults,nofail  0 2
LABEL=ESP    /boot/efi   vfat  defaults,nofail  0 1
FSTAB
sudo mkdir -p /mnt/slotA/data

# --- GRUB to the ESP, one entry per slot, by hand ------------------------
sudo mkdir -p /mnt/slotA/boot/efi
sudo mount "${NBD}p1" /mnt/slotA/boot/efi
for d in dev proc sys; do sudo mount --bind /$d /mnt/slotA/$d; done
sudo chroot /mnt/slotA grub-install --target=x86_64-efi --efi-directory=/boot/efi \
     --bootloader-id=BOOT --removable
sudo install -D -m 0644 "$HERE/boot/grub.cfg" /mnt/slotA/boot/efi/EFI/BOOT/grub.cfg
# Lesson 3, Step 2: seed the environment on the ESP, outside both slots.
sudo chroot /mnt/slotA grub-editenv /boot/efi/grubenv create
sudo chroot /mnt/slotA grub-editenv /boot/efi/grubenv set ORDER="A B" A_OK=1 A_TRY=0 B_OK=1 B_TRY=0
sudo chroot /mnt/slotA grub-editenv /boot/efi/grubenv list
for d in dev proc sys; do sudo umount /mnt/slotA/$d; done
sudo umount /mnt/slotA/boot/efi          # the ESP is not part of a slot

# --- slot B: a copy, exactly as the factory would --------------------------
sudo mount "${NBD}p3" /mnt/slotB
sudo cp -a /mnt/slotA/. /mnt/slotB/
sudo rm -rf /mnt/slotB/boot/efi/*
echo 'slot B' | sudo tee /mnt/slotB/etc/slot-id >/dev/null

# --- data partition: the things that outlive both slots ------------------
sudo mount "${NBD}p4" /mnt/slotA/data
sudo mkdir -p /mnt/slotA/data/{config,spool,rauc,containers/storage,archive,pg}
sudo umount /mnt/slotA/data

sudo umount /mnt/slotA /mnt/slotB
echo "built $DISK"; ls -la "$DISK"
echo "next: bench/boot.sh   (Ctrl-a x to quit; at the GRUB menu choose Slot B to prove independence)"
