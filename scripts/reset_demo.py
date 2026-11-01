# -*- coding: utf-8 -*-
"""重置演示数据：删除 SQLite 库文件 → 重新建表 → 重新写入 seed 演示数据。

用法：
    python scripts/reset_demo.py --yes     # 非交互式（推荐，start.bat/脚本调用）
    python scripts/reset_demo.py           # 交互式，需要输入 yes 确认

注意：会删除 data/app.db（含 -wal/-shm），不可恢复；.env / vendor / logs 不受影响。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
VENDOR = ROOT / "vendor"
if VENDOR.is_dir() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

try:  # 避免 Windows 旧控制台编码导致中文打印失败
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass


def confirm(prompt: str) -> bool:
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("yes", "y", "是")


def main() -> int:
    from app.config import settings

    db_file = settings.database_file()
    print("=" * 60)
    print(" 重置演示数据")
    print(f" 数据库：{settings.resolved_database_url()}")
    print(f" 数据文件：{db_file if db_file is not None else '（非 SQLite 文件库，跳过删除）'}")
    print("=" * 60)

    if db_file is not None and db_file.exists() and "--yes" not in sys.argv:
        if not confirm("将删除现有数据库并重建，输入 yes 继续："):
            print("已取消，未做任何修改。")
            return 1

    if db_file is not None:
        removed = []
        for candidate in (db_file, Path(str(db_file) + "-wal"), Path(str(db_file) + "-shm")):
            if candidate.exists():
                try:
                    candidate.unlink()
                    removed.append(candidate.name)
                except OSError as exc:
                    print(f"[错误] 删除 {candidate} 失败：{exc}")
                    return 1
        if removed:
            print(f"[reset] 已删除：{', '.join(removed)}")
        else:
            print("[reset] 数据库文件不存在，将直接新建。")

    # 删除后再导入 app.db，确保 Engine 指向全新文件
    from app.db import init_db

    init_db()

    seed_path = ROOT / "seed" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("seed_demo", seed_path)
    if spec is None or spec.loader is None:
        print(f"[错误] 无法加载种子脚本：{seed_path}")
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.run()

    print("[reset] 演示数据已重置完成，可执行 start.bat 启动服务。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
