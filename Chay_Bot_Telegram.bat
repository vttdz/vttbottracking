@echo off
chcp 65001 >nul
title Express Tracking - Telegram Bot
cd /d "%~dp0"

echo ========================================================
echo        EXPRESS TRACKING BOT (TELEGRAM - BẢN RÚT GỌN)
echo ========================================================
echo.

python bot_tele.py

if %errorlevel% neq 0 (
    echo.
    echo [THỬ LẠI] Đang chạy bằng lệnh py...
    py bot_tele.py
)

pause

