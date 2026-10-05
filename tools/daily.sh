#!/bin/sh
set -eu
: "${NORTHSTAR_REPO:?set NORTHSTAR_REPO to your checkout}"
: "${NORTHSTAR_UV:?set NORTHSTAR_UV to the uv executable}"
cd "$NORTHSTAR_REPO"
NORTHSTAR_DATE=$(TZ=Asia/Shanghai date +%Y-%m-%d)
exec "$NORTHSTAR_UV" run ashare daily run --account paper --date "$NORTHSTAR_DATE" --config configs/paper.yaml
