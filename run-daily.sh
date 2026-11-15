#!/bin/sh
set -eu
cd "$(dirname "$0")"
docker compose run --rm --entrypoint uv horizon run python daily_feed.py
