@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo ========================================
echo  智能去码字幕工具箱 v1.3
echo ========================================
echo.
echo  正在启动...
echo.

python "%~dp0main.py"
