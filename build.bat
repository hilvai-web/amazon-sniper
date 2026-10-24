@echo off
REM Compila Amazon Sniper en un unico AmazonSniper.exe portable (carpeta dist\)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --noconsole --name AmazonSniper ^
  --collect-all customtkinter ^
  --collect-data certifi ^
  --hidden-import aiohttp --hidden-import lxml --hidden-import lxml.etree ^
  --hidden-import bs4 --hidden-import tkinter --hidden-import sqlite3 ^
  main.py
echo.
echo Listo: dist\AmazonSniper.exe
pause
