# tests/conftest.py
import os
import sys
from pathlib import Path

# ⭐ pytest 启动时也切到项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))