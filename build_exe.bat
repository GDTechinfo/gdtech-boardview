@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === GDTech BoardView - gerando o .exe ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto erro
python tools\build_exe.py
if errorlevel 1 goto erro
echo.
echo Pronto: dist\GDTech_BoardView.exe
if not "%1"=="nopause" pause
exit /b 0
:erro
echo.
echo Ocorreu um erro. Veja as mensagens acima.
if not "%1"=="nopause" pause
exit /b 1
