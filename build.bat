@echo off
REM Gera dist\ControleBrilho.exe. Requer Python 3.10+ instalado (python.org).
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller -r requirements.txt || goto :erro
python -m PyInstaller --noconfirm --onefile --windowed --name ControleBrilho ^
  --icon icone.ico --add-data "icone.ico;." ^
  --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --hidden-import pystray._win32 ^
  brilho.py || goto :erro
echo.
echo Pronto: dist\ControleBrilho.exe
pause
exit /b 0
:erro
echo Falha na compilacao.
pause
exit /b 1
