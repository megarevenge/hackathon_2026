#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python scripts/download_weights.py
