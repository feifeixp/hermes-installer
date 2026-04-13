"""
bundle_source.py — Clone hermes-agent and create hermes_agent.zip for bundling.

Called by build.bat / build.sh before PyInstaller.
Output: hermes_agent.zip (bundled into the exe via hermes_installer.spec)

This lets the installer work without git — it extracts the zip instead of
cloning from GitHub.
"""
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path

REPO_URL  = "https://github.com/nousresearch/hermes-agent"
CLONE_DIR = Path("hermes_agent_bundle")
ZIP_OUT   = Path("hermes_agent.zip")

# Directories / files to exclude from the bundle (keep it lean)
EXCLUDE = {".git", "__pycache__", "*.pyc", "*.pyo",
           ".pytest_cache", "node_modules", ".venv", "venv"}


def _remove_readonly(func, path, _exc):
    """onerror handler for shutil.rmtree: clear read-only flag then retry."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        print(f"  [warn] could not remove {path}: {e}")


def _should_exclude(path: Path) -> bool:
    for part in path.parts:
        if part in EXCLUDE:
            return True
        for pat in EXCLUDE:
            if "*" in pat and path.name.endswith(pat.lstrip("*")):
                return True
    return False


def main():
    print(f"  → 清理旧目录 {CLONE_DIR} ...")
    if CLONE_DIR.exists():
        shutil.rmtree(CLONE_DIR, onerror=_remove_readonly)

    print(f"  → 克隆 {REPO_URL} ...")
    ret = os.system(f'git clone --depth=1 "{REPO_URL}" "{CLONE_DIR}"')
    if ret != 0:
        print("  ❌ git clone 失败，请检查网络或 git 是否已安装。")
        sys.exit(1)

    print(f"  → 创建 {ZIP_OUT} ...")
    with zipfile.ZipFile(ZIP_OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file in CLONE_DIR.rglob("*"):
            if file.is_file() and not _should_exclude(file.relative_to(CLONE_DIR)):
                arcname = file.relative_to(CLONE_DIR)
                zf.write(file, arcname)

    size_mb = ZIP_OUT.stat().st_size / 1024 / 1024
    print(f"  ✓ {ZIP_OUT} 创建完成（{size_mb:.1f} MB）")

    print(f"  → 清理临时目录 {CLONE_DIR} ...")
    shutil.rmtree(CLONE_DIR, onerror=_remove_readonly)
    print("  ✓ 源码打包完成")


if __name__ == "__main__":
    main()
