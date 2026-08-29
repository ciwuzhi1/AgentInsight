"""pytest 公共配置：把 backend 目录加入 sys.path，使 `import app.*` 可用。

全部单测不依赖 MySQL / Docker / 网络，只测纯逻辑模块。
"""
import sys
from pathlib import Path

# backend/tests/conftest.py -> parents[1] 即 backend 目录
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
