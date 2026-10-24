@echo off
REM Compila AmazonSniper.exe en local (normalmente lo hace GitHub Actions al subir cambios)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --noconsole --name AmazonSniper ^
  --collect-all customtkinter --collect-data certifi ^
  --hidden-import lxml.etree --hidden-import playwright.async_api ^
  main.py
echo.
echo Listo: dist\AmazonSniper.exe
pause
