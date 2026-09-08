#!/usr/bin/env bash
# Lesson 17, Step 4 (and Lesson 18, Steps 4–5) — build and sign a bundle.
#
#   rauc/build-bundle.sh 2026.09-1                    # good
#   rauc/build-bundle.sh 2026.09-2-broken  --broken-kernel   # Lesson 18 failure one
#   rauc/build-bundle.sh 2026.09-3-broken  --broken-config   # Lesson 18 failure two
#   rauc/build-bundle.sh 2026.09-1-rogue   --rogue           # Lesson 17 Step 6, wrong signer
#   rauc/build-bundle.sh 2026.09-1-armhf   --wrong-hardware  # Lesson 17 Step 6, compatible mismatch
#
# A signature proves who made an update. It never proves the update works:
# the --broken-* bundles are signed by the real key on purpose.
set -euo pipefail
VERSION="${1:?version}"; shift || true
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PKI="${PKI:-$HERE/pki/out}"
BENCH="${BENCH:-$HOME/edge-bench}"
WORK="${WORK:-$HOME/edge-bundle}"
ROOTFS="${ROOTFS:-$WORK/rootfs.ext4}"
NBD="${NBD:-/dev/nbd0}"
CERT="$PKI/dev.cert.pem"; KEY="$PKI/dev.key.pem"
COMPAT_SED=""

for opt in "$@"; do
  case "$opt" in
    --rogue)          CERT="$PKI/rogue.cert.pem"; KEY="$PKI/rogue.key.pem";;
    --wrong-hardware) COMPAT_SED='s/x86-64/armhf/';;
    --broken-kernel|--broken-config) BREAK="$opt";;
    *) echo "unknown option $opt" >&2; exit 2;;
  esac
done

mkdir -p "$WORK"
# 1. a root filesystem image: slot A of the bench, extracted once and reused
if [ ! -f "$ROOTFS" ]; then
    echo "extracting slot A from $BENCH/appliance.qcow2 -> $ROOTFS"
    sudo modprobe nbd max_part=8
    sudo qemu-nbd --connect="$NBD" "$BENCH/appliance.qcow2"; sleep 1
    sudo dd if="${NBD}p2" of="$ROOTFS" bs=4M status=progress
    sudo qemu-nbd --disconnect "$NBD"
    sudo chown "$USER" "$ROOTFS"
    e2fsck -fy "$ROOTFS" >/dev/null || true
    resize2fs -M "$ROOTFS"                  # shrink: an 8 GB file is a slow lesson
fi

# 2. content directory for this build
CONTENT="$WORK/content-$VERSION"
rm -rf "$CONTENT"; mkdir -p "$CONTENT"
cp --reflink=auto "$ROOTFS" "$CONTENT/rootfs.ext4"
sed -e "s/@VERSION@/$VERSION/" ${COMPAT_SED:+-e "$COMPAT_SED"} "$HERE/rauc/manifest.raucm" \
    | grep -v '^#' > "$CONTENT/manifest.raucm"

# 3. break it, if asked — inside the image, with the signature still valid
if [ -n "${BREAK:-}" ]; then
    M="$(mktemp -d)"
    sudo mount -o loop "$CONTENT/rootfs.ext4" "$M"
    case "$BREAK" in
      --broken-kernel)                       # will not boot: GRUB tries B, TRY=1, kernel dies
        sudo dd if=/dev/urandom of="$M/vmlinuz" bs=1M count=2 conv=notrunc 2>/dev/null
        # /vmlinuz is a symlink on Debian; scribble the target too
        T="$(readlink -f "$M/vmlinuz" 2>/dev/null || true)"; [ -n "$T" ] && sudo dd if=/dev/urandom of="$T" bs=1M count=2 conv=notrunc 2>/dev/null;;
      --broken-config)                       # boots perfectly and records nothing
        # The agent reads /data/config/agent.env, which is NOT in the image — so break the
        # thing that is: point the unit at an image that does not exist.
        sudo sed -i 's#^Image=.*#Image=localhost/example/does-not-exist:0#' "$M/etc/containers/systemd/vms-agent.container";;
    esac
    sudo umount "$M"; rmdir "$M"
fi

# 4. pack and sign
OUTFILE="$WORK/update-$VERSION.raucb"
rauc bundle --cert="$CERT" --key="$KEY" "$CONTENT/" "$OUTFILE"
echo; rauc info --keyring="$PKI/keyring.pem" "$OUTFILE" || echo "(rauc info refused it — as designed for --rogue)"
echo; echo "bundle: $OUTFILE"
echo "copy it into the VM (scp -P 2222 $OUTFILE root@127.0.0.1:/data/) and: rauc install /data/$(basename "$OUTFILE")"
