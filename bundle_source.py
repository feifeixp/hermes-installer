"""
Build helper: clone hermes-agent and create hermes_agent_bundle.zip
Run this before PyInstaller:  python bundle_source.py

Auto-retries with CN mirrors if GitHub is unreachable.

Set HERMES_AGENT_LOCAL_SOURCE=<path> to bundle a local checkout instead
of cloning from GitHub (for dev builds with uncommitted changes).
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

# Top-level dirs/files excluded when bundling from a local checkout.
# The cloned path doesn't contain these (fresh --depth=1 clone), so the
# exclusion only matters for the local-source path.
LOCAL_EXCLUDE_DIRS = {
    ".git", "venv", ".venv", "node_modules", "__pycache__",
    ".pytest_cache", "build", "dist", ".mypy_cache", ".ruff_cache",
    ".tox", ".idea", ".vscode", "hermes_agent.egg-info",
}
LOCAL_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".log")


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


def _zip_from_local(src: pathlib.Path) -> int:
    """Zip *src* into ZIP_PATH, excluding venv/.git/node_modules/etc."""
    src = src.resolve()
    if not src.is_dir():
        print(f"  ❌ 本地源目录不存在：{src}")
        sys.exit(1)

    print(f"  → 从本地源打包：{src}")
    count = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(src.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(src)
            # Skip if any path part is an excluded dir
            if any(part in LOCAL_EXCLUDE_DIRS for part in rel.parts):
                continue
            if f.suffix in LOCAL_EXCLUDE_SUFFIXES:
                continue
            zf.write(f, rel)
            count += 1
    return count


def main():
    # ── Local-source mode (skips clone) ────────────────────────────────────
    local_src = os.environ.get("HERMES_AGENT_LOCAL_SOURCE", "").strip()
    if local_src:
        if ZIP_PATH.exists():
            ZIP_PATH.unlink()
        count = _zip_from_local(pathlib.Path(local_src))
        size_kb = ZIP_PATH.stat().st_size // 1024
        print(f"  ✓ 打包完成（本地源）：{count} 个文件 → {ZIP_PATH}  ({size_kb} KB)")
        return

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
