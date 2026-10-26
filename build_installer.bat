@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === GDTech BoardView - gerando o instalador ===
call build_exe.bat nopause
if errorlevel 1 goto erro
for /f "delims=" %%v in ('python tools\get_version.py') do set VER=%%v
echo Versao: %VER%
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo.
  echo O Inno Setup 6 nao esta instalado. Instale com:
  echo     winget install JRSoftware.InnoSetup
  echo ou baixe em https://jrsoftware.org/isdl.php e rode este arquivo de novo.
  goto erro
)
%ISCC% /DMyAppVersion=%VER% installer\GDTechBoardView.iss
if errorlevel 1 goto erro
echo.
echo Pronto:
echo   installer\Output\GDTech_BoardView_Setup_%VER%.exe
pause
exit /b 0
:erro
echo.
echo Ocorreu um erro. Veja as mensagens acima.
pause
exit /b 1
