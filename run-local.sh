#!/bin/sh
set -eu
cd "$(dirname "$0")"
docker compose run --rm horizon
docker compose run --rm --entrypoint uv horizon run python telegram_links.py
uv run --with markdown==3.10.2 python build.py
echo 'Digest and website built. Publish data/summaries and docs through Git or the connected GitHub tools.'
