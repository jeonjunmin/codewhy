"""테스트 공용 설정.

backend/ 를 import path 에 올려 `from app.features.blame.service ...` 가 동작하게 한다.
"""

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
