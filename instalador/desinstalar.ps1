# Desinstalador do GDTech BoardView (menu Iniciar > Desinstalar, ou Configurações > Aplicativos instalados)
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { $Host.UI.RawUI.WindowTitle = 'Desinstalar o GDTech BoardView' } catch {}
Add-Type -AssemblyName System.Windows.Forms

$AppName = 'GDTech BoardView'
$Dest    = Join-Path $env:LOCALAPPDATA 'Programs\GDTech BoardView'
$Runtime = Join-Path $Dest 'runtime'
$ProgId  = 'GDTech.BoardView.Arquivo'
$Exts    = @('.tvw', '.fz', '.brd', '.bdv', '.bvr', '.cad', '.cae', '.cst', '.pcb', '.asc', '.bv', '.faz', '.gr', '.f2b')

$r = [System.Windows.Forms.MessageBox]::Show(
    "Remover o $AppName deste computador?`n`nSuas notas, recentes e configurações ficam guardadas (caso instale de novo).",
    "Desinstalar o $AppName", 'YesNo', 'Question')
if ($r -ne 'Yes') { exit 0 }

Get-Process -Name python, pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Runtime, [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

# atalhos
Remove-Item -Path (Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item -Path (Join-Path ([Environment]::GetFolderPath('Programs')) $AppName) -Recurse -Force -ErrorAction SilentlyContinue

# registro: "Abrir com", tipo de arquivo e a entrada de Aplicativos instalados
$cls = 'HKCU:\Software\Classes'
foreach ($e in $Exts) {
    Remove-ItemProperty -Path "$cls\$e\OpenWithProgids" -Name $ProgId -ErrorAction SilentlyContinue
    $k = Get-Item -Path "$cls\$e" -ErrorAction SilentlyContinue
    if ($k -and $k.GetValue('') -eq $ProgId) { Set-Item -Path "$cls\$e" -Value '' }
}
Remove-Item -Path "$cls\$ProgId" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GDTechBoardView' -Recurse -Force -ErrorAction SilentlyContinue

# a pasta do programa (este script está dentro dela: apaga depois de sair)
$cmd = "/c timeout /t 2 /nobreak >nul & rmdir /s /q `"$Dest`""
Start-Process -FilePath "$env:WINDIR\System32\cmd.exe" -ArgumentList $cmd -WindowStyle Hidden
[System.Windows.Forms.MessageBox]::Show("O $AppName foi removido.", "Desinstalar o $AppName", 'OK', 'Information') | Out-Null
exit 0
