"""出版业务 AI 系统（印制参数校验与成本测算）后端应用包。"""

import sys
from pathlib import Path

__version__ = "1.0.0"
APP_NAME = "出版业务 AI 系统-印制成本测算"
APP_VERSION = "1.0.0"

# 兼容离线部署：依赖安装在项目根 vendor/ 时，即使未设置 PYTHONPATH 也能导入
_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))
