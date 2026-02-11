@echo off
chcp 65001 >nul
echo ====================================
echo   LuLu's YouTube 下載寶
echo ====================================
echo.
echo 正在啟動伺服器...
echo 啟動後請開啟瀏覽器並前往：http://localhost:5000
echo.
echo 按 Ctrl+C 可停止伺服器
echo ====================================
echo.
python app.py
pause
