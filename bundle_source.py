"""
Build helper: clone hermes-agent and create hermes_agent_bundle.zip
Run this before PyInstaller:  python bundle_source.py
"""
import os
import stat
import subprocess
import zipfile
import pathlib
import sys
import shutil

REPO_URL  = "https://github.com/NousResearch/hermes-agent"
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
    """Cross-platform rmtree that handles Windows read-only files in .git."""
    if path.exists():
        shutil.rmtree(path, onerror=_remove_readonly)


def main():
    # ── Clean up any previous run ──────────────────────────────────────────
    print(f"  → 清理旧目录 {CLONE_DIR} ...")
    _rmtree(CLONE_DIR)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    # ── Clone (shallow, no history needed) ────────────────────────────────
    print(f"  → 克隆 {REPO_URL} ...")
    result = subprocess.run(
        ["git", "clone", "--depth=1", REPO_URL, str(CLONE_DIR)],
        check=False,
    )
    if result.returncode != 0:
        print("  ❌ git clone 失败，请检查网络连接和 git 是否已安装")
        sys.exit(1)

    # ── Zip (exclude .git to keep size small) ─────────────────────────────
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
