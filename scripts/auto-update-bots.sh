#!/bin/bash
cd /root/app || exit 0
BEFORE=$(git rev-parse HEAD)
git pull --ff-only >> /root/app/auto-update.log 2>&1 || exit 0
AFTER=$(git rev-parse HEAD)
[ "$BEFORE" = "$AFTER" ] && exit 0
echo "[$(date '+%F %T')] updated $BEFORE -> $AFTER" >> /root/app/auto-update.log
CHANGED=$(git diff --name-only "$BEFORE" "$AFTER")
if echo "$CHANGED" | grep -qE '^(requirements\.txt|Dockerfile|src/bot/)'; then
  echo "[$(date '+%F %T')] rebuilding bots" >> /root/app/auto-update.log
  docker compose up -d --build >> /root/app/auto-update.log 2>&1
fi
