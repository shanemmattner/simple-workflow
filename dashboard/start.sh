#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "Installing dependencies..."
bun install

echo "Building client..."
bun run build

echo "Starting server on port ${PORT:-4080}..."
exec bun run start
