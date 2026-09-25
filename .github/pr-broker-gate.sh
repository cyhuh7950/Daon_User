#!/usr/bin/env bash
set -euo pipefail

test -f pr-test-marker.txt
git diff --check
