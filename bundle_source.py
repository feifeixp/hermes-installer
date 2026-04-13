"""
Build helper: clone hermes-agent and create hermes_agent_bundle.zip
Run this before PyInstaller:  python bundle_source.py

Auto-retries with CN mirrors if GitHub is unreachable.
"""
import os
import stat
import subprocess
import zipfile
import pathlib
import sys
import shutil

REPO_PATH = "NousResearch/hermes-agent"

# Mirror list — tried in order until one succeeds
CLONE_URLS = [
    f"https://github.com/{REPO_PATH}",                          # original
    f"https://ghproxy.com/https://github.com/{REPO_PATH}",      # CN mirror 1
    f"https://mirror.ghproxy.com/https://github.com/{REPO_PATH}", # CN mirror 2
    f"https://gitclone.com/github.com/{REPO_PATH}",             # CN mirror 3
]

CLONE_DIR = pathlib.Path("hermes_agent_bundle")
ZIP_PATH  = pathlib.Path("hermes_agent_bundle.zip")


def _remove_readonly(func, path, _exc):
    """onerror handler: clear Windows read-only flag on .git files then retry."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        print(f"  [warn] could not remove {path}: {e}")


def _rmtree(path: pathlib.Path):
    if path.exists():
        shutil.rmtree(path, onerror=_remove_readonly)


def _try_clone() -> bool:
    """Try each mirror in order. Return True on success."""
    for i, url in enumerate(CLONE_URLS):
        label = "GitHub" if i == 0 else f"镜像 {i}"
        print(f"  → 尝试 {label}: {url} ...")
        _rmtree(CLONE_DIR)   # clean before each attempt
        result = subprocess.run(
            ["git", "clone", "--depth=1", url, str(CLONE_DIR)],
            check=False,
            timeout=120,
        )
        if result.returncode == 0:
            print(f"  ✓ 克隆成功（{label}）")
            return True
        print(f"  ✗ {label} 失败，尝试下一个...")
    return False


def main():
    # ── Clean up any previous run ──────────────────────────────────────────
    print(f"  → 清理旧目录 {CLONE_DIR} ...")
    _rmtree(CLONE_DIR)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    # ── Clone with mirror fallback ─────────────────────────────────────────
    if not _try_clone():
        print()
        print("  ❌ 所有镜像均无法访问。")
        print("     请检查网络，或手动克隆后再运行：")
        print(f"     git clone --depth=1 {CLONE_URLS[0]} {CLONE_DIR}")
        sys.exit(1)

    # ── Zip (exclude .git) ────────────────────────────────────────────────
    print("  → 创建 bundle zip ...")
    count = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(CLONE_DIR.rglob("*")):
            if f.is_file() and ".git" not in f.parts:
                arcname = f.relative_to(CLONE_DIR)
                zf.write(f, arcname)
                count += 1

    # ── Clean up clone dir ─────────────────────────────────────────────────
    print(f"  → 清理临时目录 {CLONE_DIR} ...")
    _rmtree(CLONE_DIR)

    size_kb = ZIP_PATH.stat().st_size // 1024
    print(f"  ✓ 打包完成：{count} 个文件 → {ZIP_PATH}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
