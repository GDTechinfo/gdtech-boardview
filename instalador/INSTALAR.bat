@echo off
setlocal
title Instalador do GDTech BoardView
cd /d "%~dp0"

if not exist "%~dp0programa\instalador\instalar.ps1" goto naoextraiu

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0programa\instalador\instalar.ps1"
if errorlevel 1 goto erro

echo.
echo Esta janela fecha sozinha em alguns segundos...
timeout /t 8 >nul
exit /b 0

:erro
echo.
echo Aperte qualquer tecla para fechar.
pause >nul
exit /b 1

:naoextraiu
echo.
echo  ============================================================
echo   ATENCAO: primeiro EXTRAIA o arquivo .zip!
echo  ============================================================
echo.
echo   Parece que voce abriu o INSTALAR.bat de dentro do .zip.
echo   O Windows nao consegue instalar assim.
echo.
echo   Como fazer:
echo     1. Feche esta janela.
echo     2. Clique com o botao DIREITO no arquivo .zip
echo     3. Escolha "Extrair tudo..." e depois "Extrair".
echo     4. Na pasta que abrir, de dois cliques no INSTALAR.bat
echo.
echo  Aperte qualquer tecla para fechar.
pause >nul
exit /b 1
