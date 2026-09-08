#!/bin/sh
# Lesson 9 — a 60-second H.264 test clip, the camera stand-in.
set -e
OUT="${1:-./media/clip.mp4}"
mkdir -p "$(dirname "$OUT")"
ffmpeg -y -f lavfi -i testsrc=duration=60:size=640x480:rate=30 \
       -c:v libx264 -an -g 30 -pix_fmt yuv420p "$OUT"
echo "wrote $OUT"
