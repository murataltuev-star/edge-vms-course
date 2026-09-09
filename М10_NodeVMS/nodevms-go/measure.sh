#!/bin/sh
# М10 Lesson 5, Step 5 — the rewrite argument as a number. Runs both
# controller baselines at idle and prints PSS, plus binary sizes.
# Needs: go, python3, and NODEVMS_PATH pointing at ../nodevms.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
export NODEVMS_PATH="${NODEVMS_PATH:-$HERE/../nodevms}"
cd "$HERE"
CGO_ENABLED=0 go build -ldflags="-s -w" -o /tmp/nodevms-baseline ./cmd/baseline
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -ldflags="-s -w" -o /tmp/nodevms-baseline-arm64 ./cmd/baseline
echo "go binary (x86-64, static): $(du -k /tmp/nodevms-baseline | cut -f1) kB"
echo "go binary (arm64, cross-compiled in one command): $(du -k /tmp/nodevms-baseline-arm64 | cut -f1) kB"
for i in 1 2 3; do /tmp/nodevms-baseline; done
for i in 1 2 3; do python3 cmd/pybaseline/baseline.py; done
