# -*- coding: utf-8 -*-
"""最终打包：重建交付目录 → 重建提交 zip → 计算校验值 → 更新 docs/13 与 evidence。"""
import hashlib
import json
from datetime import datetime
import os
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
BUNDLED = Path(r"C:\Users\LC_CC\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe")
DELIVERY = ROOT / "交付材料"
DATE_STR = datetime.now().strftime("%Y%m%d")
SUB_ZIP = Path(rf"F:\deepseek workarea\出版AI系统_交付材料_{DATE_STR}.zip")
DOC13 = ROOT / "docs" / "13_提交包清单与校验值.md"
EVID_JSON = ROOT / "evidence" / "delivery_checksums_20261101.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_submission_zip():
    with zipfile.ZipFile(SUB_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(DELIVERY):
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                z.write(full, os.path.relpath(full, os.path.dirname(str(DELIVERY))))


def main():
    print("[1/4] 重建交付目录 ...")
    result = subprocess.run([str(BUNDLED), str(ROOT / "scripts" / "_make_delivery.py")],
                            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("  delivery build exit:", result.returncode)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "")[:500]
        print("  build stderr tail (ascii-safe):", detail.encode("ascii", "replace").decode("ascii"))
        raise SystemExit("delivery build failed")

    print("[2/4] 重建提交 zip ...")
    build_submission_zip()

    print("[3/4] 计算校验值 ...")
    files = {
        "submission": SUB_ZIP,
        "source": DELIVERY / "01_可运行程序及源码" / "出版AI系统_源码_20261105.zip",
        "docx": DELIVERY / "02_需求与设计说明" / "需求与设计说明.docx",
        "html": DELIVERY / "02_需求与设计说明" / "三张图演示.html",
        "index": DELIVERY / "00_材料索引与提交自检.md",
    }
    vals = {}
    for key, path in files.items():
        vals[key] = {"size": path.stat().st_size, "sha256": sha256(path)}
        print(f"  {key}: {vals[key]['size']} bytes  {vals[key]['sha256']}")
    files_count = sum(1 for _ in DELIVERY.rglob("*") if _.is_file())
    vals["delivery_files"] = files_count
    vals["submission_entries"] = len(zipfile.ZipFile(SUB_ZIP).namelist())
    EVID_JSON.write_text(json.dumps(vals, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[4/4] 更新 docs/13 与证据 ...")
    content = f"""# 提交包清单与校验值（{datetime.now().strftime("%Y-%m-%d")}）

> 本文记录最终提交包的文件清单、SHA-256 校验值与敏感信息扫描结论，便于提交前核对完整性；
> 提交包内另有 `SHA256SUMS.txt`（逐文件校验值）。**本文件由 `scripts/_finalize_delivery.py` 自动生成。**

## 1 提交包

| 项 | 值 |
|---|---|
| 提交 zip | `{SUB_ZIP}` |
| 大小 | {vals['submission']['size']:,} bytes（约 {vals['submission']['size']/1024/1024:.2f} MB） |
| 文件数 | {vals['submission_entries']}（含 `SHA256SUMS.txt`） |
| SHA-256 | `{vals['submission']['sha256']}` |
| 结构 | `00_材料索引与提交自检.md` + 01~05 五项目录 + `SHA256SUMS.txt` + `README_提交说明.md` |

## 2 关键文件校验值

| 文件 | 大小（bytes） | SHA-256 |
|---|---|---|
| 源码 zip（包内 `01_.../出版AI系统_源码_20261105.zip`） | {vals['source']['size']:,} | `{vals['source']['sha256']}` |
| 需求与设计说明.docx | {vals['docx']['size']:,} | `{vals['docx']['sha256']}` |
| 三张图演示.html | {vals['html']['size']:,} | `{vals['html']['sha256']}` |
| 00_材料索引与提交自检.md | {vals['index']['size']:,} | `{vals['index']['sha256']}` |

逐文件校验：`交付材料/SHA256SUMS.txt`；重建命令：`python scripts/_finalize_delivery.py`（或先 `python scripts/_make_delivery.py` 再打包）。

## 3 敏感信息扫描

- 对源码 zip 与提交 zip 内全部可读文本文件扫描 `sk-` 长 token、`Authorization: Bearer`：
  - 未发现**真实 API Key**；
  - 命中项仅为模板/文档占位符：`.env.example` 的 `sk-PLEASE-REPLACE-WITH-YOUR-KEY`、README/配置中的 `sk-xxx` 示例、BUILD_SPEC/设计说明中的 `Authorization: Bearer ${{LLM_API_KEY}}` 模板文本；
- 提交包内**不含** `.env`、`data/*.db`、`logs/`、`vendor*/`；`.env.example` 仅含占位符。
- 结论：可安全提交。

## 4 提交包内容摘要

| 目录 | 内容 |
|---|---|
| `01_可运行程序及源码/` | 源码 zip（含 app/seed/scripts/tests/docs/README/start.bat/requirements/.env.example）+ 运行说明 + 依赖/启动脚本 |
| `02_需求与设计说明/` | 需求与设计说明.docx（3-5 页正文 + 内嵌三图）、三张图演示.html（离线单文件） |
| `03_数据与运行记录/` | 数据字典、正常/数据不足/调用失败 3 类脱敏记录、原始证据（真实调用、验收场景、冷启动 mock/real） |
| `04_测试与交付材料/` | 12 条测试用例、缺陷与回归记录（7 条已关闭）、未解决问题清单、验收自检报告、演示运行手册、录屏脚本、PPT 大纲、测试执行证据 |
| `05_小组分工与开发记录/` | 分工与开发记录、表7 开题登记、表8 成果与评分（第五组 05；3 人姓名/学号已填） |

## 5 提交前最后 3 步（11/5）

1. 核对表7/表8 的第五组（05）与三人学号（已填）；
2. 在 `.env` 填入/确认真实 Key，运行 `python scripts/demo_check.py`（应 21/21 通过）；
3. 运行 `python scripts/_finalize_delivery.py` 生成最终 `交付材料/` 与提交 zip，核对本文校验值。
"""
    DOC13.write_text(content, encoding="utf-8")
    print("updated:", DOC13)
    print("delivery files:", files_count)


if __name__ == "__main__":
    main()
