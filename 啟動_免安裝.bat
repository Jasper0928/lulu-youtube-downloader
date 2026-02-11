@echo off
chcp 65001 >nul

REM 檢查是否已有本地 Python
if exist "%~dp0python-embed\python.exe" goto :run

echo ====================================
echo   首次運行：正在準備環境...
echo ====================================
echo.

REM 下載 Python 嵌入式版本（約 10MB）
echo 正在下載 Python...
powershell -Command "& {Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.0/python-3.11.0-embed-amd64.zip' -OutFile 'python.zip'}"

REM 解壓縮
echo 正在解壓縮...
powershell -Command "& {Expand-Archive -Path 'python.zip' -DestinationPath 'python-embed' -Force}"
del python.zip

REM 啟用 pip（修改 pth 檔案）
echo import site> python-embed\python311._pth

REM 安裝 pip
echo 正在安裝套件管理器...
powershell -Command "& {Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'get-pip.py'}"
python-embed\python.exe get-pip.py
del get-pip.py

REM 安裝依賴
echo 正在安裝 Flask...
python-embed\python.exe -m pip install flask

echo.
echo 環境準備完成！
echo.
pause

:run
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

python-embed\python.exe app.py
pause
