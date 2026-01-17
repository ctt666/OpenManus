"""
测试环境导入路径修复：
部分环境下直接执行 `pytest` 时不会自动把项目根目录加入 sys.path，导致 `import app` 失败。
这里做最小侵入的兼容处理，确保 tests 中的 import 稳定可用。
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

