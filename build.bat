@echo off
REM ──────────────────────────────────────────────────────────────────────────
REM build.bat — Build Hermes Installer as Windows .exe + .zip
REM Usage: Double-click or run in CMD / PowerShell
REM Requires: Python 3.10+ in PATH, pip
REM ──────────────────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

set APP_NAME=Hermes Installer
set DIST_DIR=dist
set ZIP_NAME=Hermes-Installer-Windows.zip

echo.
echo  ⚡ Hermes Agent Installer — Windows 打包脚本
echo  ──────────────────────────────────────────────

REM ── 1. Check Python ───────────────────────────────────────────────────────
echo  → 检查 Python 版本...
python --version 2>nul
if errorlevel 1 (
    echo  ❌ 未找到 Python，请先安装 Python 3.10+
    echo     下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM ── 2. Install deps ───────────────────────────────────────────────────────
echo  → 安装打包依赖...
pip install --quiet ^
    pywebview ^
    pyinstaller ^
    fastapi ^
    "uvicorn[standard]" ^
    aiohttp ^
    qrcode ^
    pillow ^
    pydantic ^
    pyyaml ^
    python-dotenv

if errorlevel 1 (
    echo  ❌ 依赖安装失败
    pause
    exit /b 1
)

REM ── 3. Clean ──────────────────────────────────────────────────────────────
echo  → 清理旧构建...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__

REM ── 4. PyInstaller ────────────────────────────────────────────────────────
echo  → 运行 PyInstaller (需要 2-5 分钟)...
python -m PyInstaller hermes_installer.spec --noconfirm --clean

if errorlevel 1 (
    echo  ❌ PyInstaller 构建失败
    pause
    exit /b 1
)

REM ── 5. Verify .exe ────────────────────────────────────────────────────────
set EXE_PATH=%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe
if not exist "%EXE_PATH%" (
    echo  ❌ 构建失败: %EXE_PATH% 未找到
    pause
    exit /b 1
)
echo  ✓ .exe 构建完成

REM ── 6. Create .zip ────────────────────────────────────────────────────────
echo  → 创建 .zip 压缩包...
powershell -Command ^
    "Compress-Archive -Path '%DIST_DIR%\%APP_NAME%' -DestinationPath '%DIST_DIR%\%ZIP_NAME%' -Force"

if errorlevel 1 (
    echo  ⚠ zip 创建失败，但 .exe 文件仍可使用
) else (
    echo  ✓ .zip 创建完成
)

echo.
echo  ──────────────────────────────────────────────
echo  ✅ Windows 打包完成！
echo.
echo     .exe  →  %DIST_DIR%\%APP_NAME%\%APP_NAME%.exe
echo     .zip  →  %DIST_DIR%\%ZIP_NAME%
echo.
echo  分发说明：
echo    1. 将 %ZIP_NAME% 发给 Windows 用户
echo    2. 解压后双击 "%APP_NAME%.exe" 运行
echo    3. 如 SmartScreen 弹出警告，点"更多信息"→"仍要运行"
echo    4. Windows 11 内置 Edge WebView2，无需额外安装
echo    5. Windows 10 用户如遇问题，需安装 Edge WebView2 Runtime:
echo       https://developer.microsoft.com/en-us/microsoft-edge/webview2/
echo  ──────────────────────────────────────────────
echo.
pause
