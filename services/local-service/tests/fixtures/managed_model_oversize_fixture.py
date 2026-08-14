from __future__ import annotations

import sys


sys.stdin.buffer.read()
sys.stdout.buffer.write(b"x" * (2 * 1024 * 1024 + 1))
