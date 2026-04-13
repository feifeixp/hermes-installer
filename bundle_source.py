"""
Build helper: clone hermes-agent and create hermes_agent_bundle.zip
Run this before PyInstaller:  python bundle_source.py
"""
import subprocess
import zipfile
import pathlib
import sys
import shutil

REPO_URL  = "https://github.com/NousResearch/hermes-agent"
CLONE_DIR = pathlib.Path("hermes_agent_bundle")
ZIP_PATH  = pathlib.Path("hermes_agent_bundle.zip")


def main():
    # ── Clean up any previous run ─────────────────────────────────────────
    if CLONE_DIR.exists():
        shutil.rmtree(CLONE_DIR)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    # ── Clone (shallow, no history needed) ───────────────────────────────
    print(f"→ Cloning {REPO_URL} ...")
    result = subprocess.run(
        ["git", "clone", "--depth=1", REPO_URL, str(CLONE_DIR)],
        check=False,
    )
    if result.returncode != 0:
        print("❌ git clone failed — check your network / git config")
        sys.exit(1)

    # ── Zip (exclude .git to keep size small) ────────────────────────────
    print("→ Creating bundle zip ...")
    count = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(CLONE_DIR.rglob("*")):
            if f.is_file() and ".git" not in f.parts:
                arcname = f.relative_to(CLONE_DIR)
                zf.write(f, arcname)
                count += 1

    # ── Clean up clone dir ────────────────────────────────────────────────
    shutil.rmtree(CLONE_DIR)
    size_kb = ZIP_PATH.stat().st_size // 1024
    print(f"✓ Bundled {count} files → {ZIP_PATH}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
