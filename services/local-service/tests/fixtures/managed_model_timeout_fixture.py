from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(sys.argv[1]).write_text(str(child.pid), encoding="ascii")
time.sleep(60)
