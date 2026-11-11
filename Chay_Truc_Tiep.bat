@echo off
chcp 65001 >nul
title Express Tracking - Chạy Trực Tiếp Không Cần Cài Đặt
cd /d "%~dp0"

echo ========================================================
echo        EXPRESS TRACKING BOT - CHẠY TRỰC TIẾP
echo ========================================================
echo.
echo [*] Dang khoi dong ung dung tra cuu don hang...
echo [*] May chu se tu dong mo tren trinh duyet...
echo.

python app_local.py

if %errorlevel% neq 0 (
    echo.
    echo [LỖI] Không thể chạy bằng python. Đang thử bằng py...
    py app_local.py
)

pause

