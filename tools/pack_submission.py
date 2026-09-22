#!/usr/bin/env python
"""提交包打包与结构校验（赛题二 阶段 5）。

赛题要求：`eda_2026_case_1.tar.gz`，解包后顶层必须是 `eda_2026_case_1/`，
内含 `optimization.py`，且能被 `./run_score.sh eda_2026_case_1.tar.gz` 跑通。

用法:
    python tools/pack_submission.py                      # 打包到 dist/
    python tools/pack_submission.py --verify-only        # 只校验已有包
    python tools/pack_submission.py --check-dir eda_2026_case_1

在 Windows 上也能直接产出 tar.gz（用标准库 tarfile），上传服务器即可。
"""

import argparse
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = "eda_2026_case_1"

# 必须进包的源文件（相对仓库根）
REQUIRED = [
    "optimization.py",
    "q3_optimizer/__init__.py",
    "q3_optimizer/optimize.py",
    "q3_optimizer/paramfile.py",
    "q3_optimizer/simulator.py",
]
# 可选携带（便于评委理解，不影响运行）
OPTIONAL = ["README.md", "docs/server_onboarding_plan.md",
            "docs/server_assets.md"]
# 明确排除
EXCLUDE_SUFFIX = (".pyc", ".pyo")
EXCLUDE_DIRS = {"__pycache__", "_scratch", ".git"}


def iter_files():
    for rel in REQUIRED:
        p = ROOT / rel
        if not p.exists():
            raise FileNotFoundError(f"缺少必需文件: {rel}")
        yield rel, p
    for rel in OPTIONAL:
        p = ROOT / rel
        if p.exists():
            yield rel, p


def verify_layout(names):
    """校验包内路径结构，返回问题列表。"""
    problems = []
    if not names:
        return ["包内没有任何文件"]
    tops = {n.split("/")[0] for n in names}
    if tops != {TOP}:
        problems.append(f"顶层目录必须是唯一的 {TOP}/，实际为 {sorted(tops)}")
    if f"{TOP}/optimization.py" not in names:
        problems.append(f"缺少 {TOP}/optimization.py（赛题要求入口文件）")
    for n in names:
        if any(part in EXCLUDE_DIRS for part in n.split("/")):
            problems.append(f"包含不应提交的路径: {n}")
        if n.endswith(EXCLUDE_SUFFIX):
            problems.append(f"包含不应提交的文件: {n}")
    return problems


def pack(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tar_path = out / f"{TOP}.tar.gz"
    names = []
    with tarfile.open(tar_path, "w:gz") as tf:
        for rel, src in iter_files():
            arc = f"{TOP}/{rel}"
            tf.add(src, arcname=arc)
            names.append(arc)
    return tar_path, names


def read_names(tar_path):
    with tarfile.open(tar_path, "r:gz") as tf:
        return [m.name for m in tf.getmembers() if m.isfile()]


def main(argv=None):
    ap = argparse.ArgumentParser(description="提交包打包与结构校验")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--check-dir", default=None, help="校验一个已解包的目录")
    ap.add_argument("--tar", default=None, help="校验一个已有的 tar.gz")
    args = ap.parse_args()

    if args.check_dir:
        d = Path(args.check_dir)
        names = [f"{TOP}/{p.relative_to(d).as_posix()}"
                 for p in d.rglob("*") if p.is_file()]
    elif args.tar:
        names = read_names(args.tar)
    elif args.verify_only:
        tar_path = Path(args.out) / f"{TOP}.tar.gz"
        if not tar_path.exists():
            print(f"找不到 {tar_path}", file=sys.stderr)
            return 2
        names = read_names(tar_path)
    else:
        tar_path, names = pack(args.out)
        print(f"[pack] 已生成 {tar_path}（{len(names)} 个文件）")

    problems = verify_layout(names)
    for n in sorted(names):
        print(f"   {n}")
    if problems:
        print("\n结构校验: 失败")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\n结构校验: 通过（顶层 eda_2026_case_1/ 且含 optimization.py）")
    print("上传后用 ./run_score.sh eda_2026_case_1.tar.gz 自测一遍。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
