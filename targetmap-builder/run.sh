#!/usr/bin/env bash
# One command to launch the Outbound List Maker UI.
# Creates a local virtualenv on first run, then starts the web app.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "First run: setting up local environment…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet pyyaml
fi

exec ./.venv/bin/python scripts/app.py "$@"
