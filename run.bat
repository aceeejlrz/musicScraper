@echo off
echo.
echo  ========================================
echo   YouTube Playlist Downloader
echo   http://localhost:5050
echo  ========================================
echo.

:: Install / update dependencies in venv
echo  Installing dependencies...
".venv\Scripts\python.exe" -m pip install flask flask-cors yt-dlp --quiet

echo  Starting server...
echo.

:: Run using the venv python
".venv\Scripts\python.exe" app.py

pause
