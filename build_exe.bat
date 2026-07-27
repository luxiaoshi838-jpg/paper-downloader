@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python Launcher。请先安装 Python 3.11 或更高版本。
  pause
  exit /b 1
)

py -m pip install --upgrade pip
if errorlevel 1 goto :error
py -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

py -m PyInstaller --noconfirm --clean --onefile --windowed --name "DOI文献批量下载器" app.py
if errorlevel 1 goto :error

echo.
echo 构建完成：%CD%\dist\DOI文献批量下载器.exe
pause
exit /b 0

:error
echo.
echo 构建失败，请查看上方错误信息。
pause
exit /b 1
