@echo off
REM ──────────────────────────────────────────────────────────────────────────
REM build.bat — Build Hermes Installer as Windows single-file .exe
REM Usage: Double-click or run in CMD / PowerShell as Administrator
REM Requires: Python 3.10+ (not RC), pip
REM ──────────────────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

set APP_NAME=Hermes Installer
set DIST_DIR=dist
set ZIP_NAME=Hermes-Installer-Windows.zip
set EXE_PATH=%DIST_DIR%\%APP_NAME%.exe

echo.
echo  ⚡ Hermes Agent Installer — Windows 打包脚本
echo  ──────────────────────────────────────────────

REM ── 1. Check Python ───────────────────────────────────────────────────────
echo  → 检查 Python 版本...
python --version 2>nul
if errorlevel 1 (
    echo  ❌ 未找到 Python，请先安装 Python 3.10+（正式版，非 RC）
    echo     下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM ── 2. Upgrade pip first ──────────────────────────────────────────────────
echo  → 升级 pip...
python -m pip install --upgrade pip --quiet

REM ── 3. Install deps (retry once on failure) ───────────────────────────────
echo  → 安装打包依赖（首次约 1-2 分钟）...

:install_attempt
python -m pip install --quiet ^
    "pywebview>=4.0" ^
    "pyinstaller>=6.0" ^
    "fastapi>=0.100" ^
    "uvicorn[standard]>=0.23" ^
    "aiohttp>=3.9" ^
    "qrcode>=8.0" ^
    "pillow>=10.0" ^
    "pydantic>=2.0" ^
    "pyyaml>=6.0" ^
    "python-dotenv>=1.0"

if errorlevel 1 (
    echo  ⚠ 首次安装失败，正在重试（可能因文件锁）...
    REM 清理 pip 缓存中的锁文件
    python -m pip cache purge >nul 2>&1
    ping -n 3 127.0.0.1 >nul
    python -m pip install --quiet --no-cache-dir ^
        "pywebview>=4.0" ^
        "pyinstaller>=6.0" ^
        "fastapi>=0.100" ^
        "uvicorn[standard]>=0.23" ^
        "aiohttp>=3.9" ^
        "qrcode>=8.0" ^
        "pillow>=10.0" ^
        "pydantic>=2.0" ^
        "pyyaml>=6.0" ^
        "python-dotenv>=1.0"
    if errorlevel 1 (
        echo  ❌ 依赖安装失败，请以管理员身份运行或手动安装依赖
        pause
        exit /b 1
    )
)
echo  ✓ 依赖安装完成

REM ── 3.5 Bundle hermes-agent source into the installer ────────────────────
echo  → 打包 hermes-agent 源码（内置到安装器，用户无需 git clone）...
python bundle_source.py
if errorlevel 1 (
    echo  ❌ 源码打包失败，请检查网络连接和 git 配置后重试
    pause
    exit /b 1
)
echo  ✓ hermes-agent 源码打包完成

REM ── 4. Kill running exe + clean previous build ───────────────────────────
echo  → 关闭正在运行的旧版本（如有）...
taskkill /f /im "%APP_NAME%.exe" >nul 2>&1
ping -n 2 127.0.0.1 >nul

echo  → 清理旧构建...
if exist build rmdir /s /q build
if exist "%DIST_DIR%\%APP_NAME%.exe" (
    del /f /q "%DIST_DIR%\%APP_NAME%.exe" >nul 2>&1
    if exist "%DIST_DIR%\%APP_NAME%.exe" (
        echo  ⚠ 文件仍被占用，等待 3 秒后重试...
        ping -n 4 127.0.0.1 >nul
        del /f /q "%DIST_DIR%\%APP_NAME%.exe" >nul 2>&1
    )
)
if exist "%DIST_DIR%\%ZIP_NAME%"     del /f /q "%DIST_DIR%\%ZIP_NAME%"
if exist __pycache__ rmdir /s /q __pycache__

REM ── 5. PyInstaller (onefile mode) ─────────────────────────────────────────
echo  → 运行 PyInstaller（单文件模式，需要 3-8 分钟）...
python -m PyInstaller hermes_installer.spec --noconfirm --clean

if errorlevel 1 (
    echo  ❌ PyInstaller 构建失败，请检查上方错误信息
    pause
    exit /b 1
)

REM ── 6. Verify output ──────────────────────────────────────────────────────
if not exist "%EXE_PATH%" (
    echo  ❌ 构建失败：%EXE_PATH% 未找到
    echo     请检查 dist\ 目录中的实际文件名
    dir "%DIST_DIR%\" 2>nul
    pause
    exit /b 1
)
echo  ✓ .exe 构建完成: %EXE_PATH%

REM ── 7. Create .zip ────────────────────────────────────────────────────────
echo  → 创建 .zip 压缩包...
powershell -Command ^
    "Compress-Archive -Path '%EXE_PATH%' -DestinationPath '%DIST_DIR%\%ZIP_NAME%' -Force"

if errorlevel 1 (
    echo  ⚠ zip 创建失败，但 .exe 文件仍可使用
) else (
    echo  ✓ .zip 创建完成: %DIST_DIR%\%ZIP_NAME%
)

echo.
echo  ──────────────────────────────────────────────
echo  ✅ Windows 打包完成！
echo.
echo     单文件 .exe → %EXE_PATH%
echo     压缩包      → %DIST_DIR%\%ZIP_NAME%
echo.
echo  分发说明：
echo    1. 将 %ZIP_NAME% 发给用户，解压后直接双击 .exe 运行
echo    2. 如 SmartScreen 弹出警告，点"更多信息"→"仍要运行"
echo    3. 首次启动约需 10-20 秒（单文件模式自解压）
echo    4. Windows 11 内置 Edge WebView2，无需额外安装
echo    5. Windows 10 用户如遇黑屏，请安装:
echo       https://developer.microsoft.com/en-us/microsoft-edge/webview2/
echo  ──────────────────────────────────────────────
echo.
pause
