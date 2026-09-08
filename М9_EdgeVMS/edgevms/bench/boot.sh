#!/usr/bin/env bash
# Lesson 16, Step 6 — boot the bench. Ctrl-a x quits; Ctrl-a c is the monitor.
# The monitor is also on a socket so bench/outage.sh can pull the cable.
set -euo pipefail
BENCH="${BENCH:-$HOME/edge-bench}"
DISK="$BENCH/appliance.qcow2"
OVMF_CODE="${OVMF_CODE:-/usr/share/OVMF/OVMF_CODE.fd}"
[ -f "$BENCH/OVMF_VARS.fd" ] || cp "${OVMF_VARS:-/usr/share/OVMF/OVMF_VARS.fd}" "$BENCH/OVMF_VARS.fd"

KVM=()
if [ -r /dev/kvm ]; then KVM=(-enable-kvm); else echo "no /dev/kvm: full emulation, slower, still correct" >&2; fi

exec qemu-system-x86_64 "${KVM[@]}" \
  -m "${MEM:-2048}" -smp "${SMP:-2}" \
  -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
  -drive if=pflash,format=raw,file="$BENCH/OVMF_VARS.fd" \
  -drive file="$DISK",format=qcow2,if=virtio \
  -netdev user,id=net0,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:8080-:8080 \
  -device virtio-net-pci,netdev=net0,id=nic0 \
  -monitor unix:"$BENCH/monitor.sock",server,nowait \
  -nographic "$@"
