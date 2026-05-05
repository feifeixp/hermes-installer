#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# build.sh — Build Hermes Installer as macOS .app + .dmg
# Usage: bash build.sh
# Requires: Python 3.10+, pip
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Always run from the directory that contains this script,
# regardless of where the user invoked it from.
cd "$(dirname "$0")"

DIST_DIR="dist"
APP_NAME="Hermes Installer"
DMG_NAME="Hermes-Installer-macOS.dmg"
VOLUME_NAME="Hermes Installer"

echo ""
echo "⚡ Hermes Installer — macOS 打包脚本"
echo "────────────────────────────────────────────"

# ── 1. Find Python 3.10+ ──────────────────────────────────────────────────
echo "→ 查找 Python 3.10+..."
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        VER=$($candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        MAJ=$(echo "$VER" | cut -d. -f1)
        MIN=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJ" -ge 3 ] && [ "$MIN" -ge 10 ]; then
            PYTHON=$candidate
            echo "   ✓ 使用 $PYTHON (Python $VER)"
            break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python 3.10+，请安装后重试"
    exit 1
fi

# ── 2. Install build deps ──────────────────────────────────────────────────
echo "→ 创建构建虚拟环境..."
BUILD_VENV=".build_venv"
if [ ! -d "$BUILD_VENV" ]; then
    $PYTHON -m venv "$BUILD_VENV"
fi
source "$BUILD_VENV/bin/activate"
PYTHON="$BUILD_VENV/bin/python"

echo "→ 安装打包依赖..."
$PYTHON -m pip install --quiet \
    pywebview \
    pyinstaller

# ── 3. Bundle hermes-agent source (optional — falls back to git-clone at runtime) ──
echo "→ 打包 hermes-agent 源码（需要联网，失败时安装器会自动 git clone）..."
if $PYTHON bundle_source.py; then
    echo "   ✓ hermes_agent_bundle.zip 已生成"
else
    echo "   ⚠ bundle_source.py 失败（可能无网络），跳过离线包"
    echo "     安装器运行时将自动从 GitHub 克隆"
fi

# ── 4. Clean previous build ────────────────────────────────────────────────
echo "→ 清理旧构建..."
rm -rf build/ dist/ __pycache__/

# ── 5. PyInstaller ────────────────────────────────────────────────────────
echo "→ 运行 PyInstaller (需要 1-3 分钟)..."
$PYTHON -m PyInstaller hermes_installer.spec --noconfirm --clean

# ── 6. Verify .app ────────────────────────────────────────────────────────
APP_PATH="$DIST_DIR/$APP_NAME.app"
if [ ! -d "$APP_PATH" ]; then
    echo "❌ 构建失败: $APP_PATH 未找到"
    exit 1
fi
echo "✓ .app 构建完成"

# ── 7. Create .dmg ────────────────────────────────────────────────────────
echo "→ 创建 .dmg 安装包..."

TMP_DMG_DIR=$(mktemp -d)
cp -r "$APP_PATH" "$TMP_DMG_DIR/"
ln -s /Applications "$TMP_DMG_DIR/Applications"

hdiutil create \
    -volname "$VOLUME_NAME" \
    -srcfolder "$TMP_DMG_DIR" \
    -ov \
    -format UDZO \
    -imagekey zlib-level=9 \
    "$DIST_DIR/$DMG_NAME" \
    2>/dev/null

rm -rf "$TMP_DMG_DIR"

APP_SIZE=$(du -sh "$APP_PATH" | cut -f1)
DMG_SIZE=$(du -sh "$DIST_DIR/$DMG_NAME" | cut -f1)

echo ""
echo "────────────────────────────────────────────"
echo "✅ macOS 打包完成！"
echo ""
echo "   .app  →  $APP_PATH  ($APP_SIZE)"
echo "   .dmg  →  $DIST_DIR/$DMG_NAME  ($DMG_SIZE)"
echo ""
echo "分发说明："
echo "  1. 将 $DMG_NAME 发给用户"
echo "  2. 双击 .dmg，把 Hermes Installer 拖入 Applications"
echo "  3. 首次运行: 右键 → 打开 (绕过 Gatekeeper 未签名警告)"
echo "────────────────────────────────────────────"
