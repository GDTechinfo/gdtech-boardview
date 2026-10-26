# Instalador do GDTech BoardView — criado por GDTech Informática
#
# Chamado pelo INSTALAR.bat. Instala só para o usuário atual (não precisa ser administrador), em
#   %LOCALAPPDATA%\Programs\GDTech BoardView
# com um Python próprio e portátil dentro da pasta do programa (não mexe em nenhum outro Python do PC).
# Pode ser rodado de novo a qualquer momento: atualiza o programa e conserta a instalação.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072 } catch {}
try { $Host.UI.RawUI.WindowTitle = 'Instalando o GDTech BoardView' } catch {}

$AppName = 'GDTech BoardView'
$PyVer   = '3.12.10'
$Origem  = Split-Path -Parent $PSScriptRoot             # pasta "programa" (dentro do zip extraído)
$Raiz    = Split-Path -Parent $Origem                   # pasta do zip extraído
$Dest    = Join-Path $env:LOCALAPPDATA 'Programs\GDTech BoardView'
$Runtime = Join-Path $Dest 'runtime'
$Py      = Join-Path $Runtime 'python.exe'
$PyW     = Join-Path $Runtime 'pythonw.exe'
$Script  = Join-Path $Dest 'gdtech_boardview.py'
$Icone   = Join-Path $Dest 'gdtech_boardview.ico'
$Tmp     = Join-Path $env:TEMP 'GDTechBoardView_instalacao'
$Log     = Join-Path $env:TEMP 'GDTech_BoardView_instalacao.log'
$Exts    = @('.tvw', '.fz', '.brd', '.bdv', '.bvr', '.cad', '.cae', '.cst', '.pcb', '.asc', '.bv', '.faz', '.gr', '.f2b')
$ProgId  = 'GDTech.BoardView.Arquivo'

try { Start-Transcript -Path $Log -Force | Out-Null } catch {}

function Etapa($n, $t) { Write-Host ''; Write-Host "[$n/7] $t" -ForegroundColor Cyan }
function Ok($t)        { Write-Host "      OK - $t" -ForegroundColor Green }
function Info($t)      { Write-Host "      $t" -ForegroundColor Gray }

function Nativo([scriptblock]$bloco) {
    # programas de fora (curl, python, pip) escrevem avisos no stderr; no PowerShell 5.1 isso não pode
    # derrubar a instalação: quem decide se deu certo é o código de saída ($LASTEXITCODE)
    $antes = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $bloco } finally { $ErrorActionPreference = $antes }
}

function Baixar($url, $arquivo) {
    # curl.exe já vem no Windows 10/11 e mostra uma barra de progresso
    $curl = Join-Path $env:WINDIR 'System32\curl.exe'
    if (Test-Path $curl) {
        Nativo { & $curl -L --fail --retry 3 --retry-delay 2 --progress-bar -o $arquivo $url }
        if ($LASTEXITCODE -eq 0 -and (Test-Path $arquivo)) { return }
        # alguns antivírus/redes bloqueiam a checagem de revogação do certificado
        Nativo { & $curl -L --fail --retry 3 --retry-delay 2 --ssl-no-revoke --progress-bar -o $arquivo $url }
        if ($LASTEXITCODE -eq 0 -and (Test-Path $arquivo)) { return }
    }
    Invoke-WebRequest -Uri $url -OutFile $arquivo -UseBasicParsing
    if (-not (Test-Path $arquivo)) { throw "Não consegui baixar $url" }
}

function Atalho($lnk, $alvo, $argumentos, $icone, $descricao, $pasta) {
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = $alvo
    $s.Arguments = $argumentos
    $s.WorkingDirectory = $pasta
    if ($icone) { $s.IconLocation = "$icone,0" }
    $s.Description = $descricao
    $s.Save()
}

function TemDll($nome) {
    return (Test-Path (Join-Path $env:WINDIR "System32\$nome"))
}

try {
    Write-Host ''
    Write-Host '  =========================================================' -ForegroundColor Yellow
    Write-Host '     GDTech BoardView - instalação                        ' -ForegroundColor Yellow
    Write-Host '     criado por GDTech Informática                        ' -ForegroundColor Yellow
    Write-Host '  =========================================================' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Na primeira vez o instalador baixa uns 120 MB (Python e bibliotecas).'
    Write-Host '  Deixe esta janela aberta: pode levar de 2 a 10 minutos, dependendo da internet.'

    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'Este programa precisa do Windows 64 bits (o seu é 32 bits).'
    }
    $arq = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arq = $env:PROCESSOR_ARCHITEW6432 }
    $arm = ($arq -eq 'ARM64')
    $pySufixo = if ($arm) { 'arm64' } else { 'amd64' }
    $vcArq    = if ($arm) { 'arm64' } else { 'x64' }
    New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

    # ---------------------------------------------------------------- 1. copiar o programa
    Etapa 1 'Copiando o programa'
    $abertos = Get-Process -Name python, pythonw -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($Runtime, [StringComparison]::OrdinalIgnoreCase) }
    if ($abertos) {
        Info 'O GDTech BoardView está aberto: fechando para atualizar...'
        $abertos | Stop-Process -Force
        Start-Sleep -Seconds 2
    }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    Nativo { robocopy $Origem $Dest /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null }
    if ($LASTEXITCODE -ge 8) { throw "Não consegui copiar os arquivos para $Dest (robocopy $LASTEXITCODE)." }
    $manual = Get-ChildItem -Path $Raiz -Filter '*.pdf' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($manual) { Copy-Item $manual.FullName (Join-Path $Dest 'Manual do GDTech BoardView.pdf') -Force }
    Get-ChildItem -Path $Dest -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike "$Runtime*" } | Unblock-File -ErrorAction SilentlyContinue
    Ok "programa em $Dest"

    # ---------------------------------------------------------------- 2. Python próprio
    Etapa 2 "Python $PyVer (só para este programa)"
    if (-not (Test-Path $Py)) {
        $zip = Join-Path $Tmp "python-$PyVer-embed-$pySufixo.zip"
        Info 'baixando o Python...'
        Baixar "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-$pySufixo.zip" $zip
        New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
        Expand-Archive -Path $zip -DestinationPath $Runtime -Force
    }
    # o Python "embutível" ignora a pasta do programa e o site-packages: libera os dois
    $pth = Get-ChildItem -Path $Runtime -Filter 'python*._pth' | Select-Object -First 1
    if (-not $pth) { throw 'Python incompleto (arquivo ._pth não encontrado). Rode o INSTALAR.bat de novo.' }
    $zipStd = (Get-ChildItem -Path $Runtime -Filter 'python3*.zip' | Select-Object -First 1).Name
    [IO.File]::WriteAllLines($pth.FullName, @($zipStd, '.', '..', 'import site'))
    if (-not (Test-Path $Py)) { throw 'O Python não foi instalado corretamente.' }
    Ok ((Nativo { & $Py --version 2>&1 }) | Out-String).Trim()

    # ---------------------------------------------------------------- 3. pip
    Etapa 3 'Instalador de bibliotecas (pip)'
    Nativo { & $Py -m pip --version *> $null }
    if ($LASTEXITCODE -ne 0) {
        $gp = Join-Path $Tmp 'get-pip.py'
        Info 'baixando o pip...'
        Baixar 'https://bootstrap.pypa.io/get-pip.py' $gp
        Nativo { & $Py $gp --no-warn-script-location --disable-pip-version-check }
        if ($LASTEXITCODE -ne 0) { throw 'Não consegui instalar o pip.' }
    }
    Ok 'pip pronto'

    # ---------------------------------------------------------------- 4. bibliotecas
    Etapa 4 'Bibliotecas (Qt, numpy, OpenGL, leitor de PDF) - a parte mais demorada'
    $req = Join-Path $Dest 'instalador\requisitos.txt'
    $pipArgs = @('-m', 'pip', 'install', '--upgrade', '--no-warn-script-location', '--disable-pip-version-check',
                 '--prefer-binary', '-r', $req)
    Nativo { & $Py @pipArgs }
    if ($LASTEXITCODE -ne 0) {
        Info 'deu erro, tentando de novo...'
        Start-Sleep -Seconds 3
        Nativo { & $Py @pipArgs }
        if ($LASTEXITCODE -ne 0) { throw 'Não consegui baixar as bibliotecas. Confira a internet e rode o INSTALAR.bat de novo.' }
    }
    Ok 'bibliotecas instaladas'

    # ---------------------------------------------------------------- 5. runtime da Microsoft
    Etapa 5 'Componentes do Windows (Visual C++)'
    if (-not ((TemDll 'msvcp140.dll') -and (TemDll 'vcruntime140.dll') -and (TemDll 'vcruntime140_1.dll'))) {
        $vc = Join-Path $Tmp "vc_redist.$vcArq.exe"
        Info 'faltando no Windows: baixando o Visual C++ da Microsoft...'
        Baixar "https://aka.ms/vs/17/release/vc_redist.$vcArq.exe" $vc
        Info 'instalando (o Windows pode perguntar se permite - clique em SIM)...'
        $p = Start-Process -FilePath $vc -ArgumentList '/install', '/passive', '/norestart' -Wait -PassThru
        if ($p.ExitCode -notin 0, 1638, 3010) { Info "o instalador do Visual C++ terminou com código $($p.ExitCode)" }
    }
    Ok 'componentes presentes'

    # ---------------------------------------------------------------- 6. teste
    Etapa 6 'Conferindo se está tudo funcionando'
    # o teste vai num arquivo .py: o PowerShell 5.1 tira as aspas de textos passados para outros programas
    $teste = Join-Path $Tmp 'teste_instalacao.py'
    [IO.File]::WriteAllLines($teste, @(
        'import PySide6.QtWidgets, PySide6.QtOpenGLWidgets, numpy, OpenGL, pypdfium2, Crypto',
        'import gdboard.main',
        'print("tudo certo")'))
    Push-Location $Dest
    $saida = ((Nativo { & $Py $teste 2>&1 }) | Out-String).Trim()
    $cod = $LASTEXITCODE
    Pop-Location
    if ($cod -ne 0) { throw "O teste falhou:`n$saida" }
    Ok $saida

    # ---------------------------------------------------------------- 7. atalhos e Windows
    Etapa 7 'Atalhos, menu Iniciar e "Abrir com"'
    $versao = '?'
    $m = Select-String -Path (Join-Path $Dest 'gdboard\main.py') -Pattern 'APP_VERSION = "([^"]+)"' | Select-Object -First 1
    if ($m) { $versao = $m.Matches[0].Groups[1].Value }
    $arg = '"' + $Script + '"'
    $desk = [Environment]::GetFolderPath('Desktop')
    $menu = Join-Path ([Environment]::GetFolderPath('Programs')) $AppName
    New-Item -ItemType Directory -Force -Path $menu | Out-Null
    Atalho (Join-Path $desk "$AppName.lnk") $PyW $arg $Icone 'Visualizador de boardview - GDTech Informática' $Dest
    Atalho (Join-Path $menu "$AppName.lnk") $PyW $arg $Icone 'Visualizador de boardview - GDTech Informática' $Dest
    $manualDest = Join-Path $Dest 'Manual do GDTech BoardView.pdf'
    if (Test-Path $manualDest) {
        Atalho (Join-Path $menu 'Manual do GDTech BoardView.lnk') $manualDest '' '' 'Manual do programa' $Dest
    }
    $ps = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $desinst = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Dest 'instalador\desinstalar.ps1') + '"'
    Atalho (Join-Path $menu "Desinstalar o $AppName.lnk") $ps $desinst $Icone 'Remove o programa' $Dest

    # aparece em Configurações > Aplicativos instalados
    $un = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GDTechBoardView'
    New-Item -Path $un -Force | Out-Null
    $kb = [int]((Get-ChildItem $Dest -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1024)
    $vals = @{
        DisplayName = $AppName; DisplayVersion = $versao; Publisher = 'GDTech Informática'
        DisplayIcon = $Icone; InstallLocation = $Dest; UninstallString = "`"$ps`" $desinst"
        URLInfoAbout = 'https://youtube.com/GDTechinformatica'
    }
    foreach ($k in $vals.Keys) { Set-ItemProperty -Path $un -Name $k -Value $vals[$k] }
    Set-ItemProperty -Path $un -Name EstimatedSize -Value $kb -Type DWord
    Set-ItemProperty -Path $un -Name NoModify -Value 1 -Type DWord
    Set-ItemProperty -Path $un -Name NoRepair -Value 1 -Type DWord

    # "Abrir com": os boardviews passam a abrir com dois cliques (quando nenhum outro programa já é o padrão)
    $cls = 'HKCU:\Software\Classes'
    New-Item -Path "$cls\$ProgId\DefaultIcon" -Force | Out-Null
    New-Item -Path "$cls\$ProgId\shell\open\command" -Force | Out-Null
    Set-Item -Path "$cls\$ProgId" -Value 'Boardview (GDTech BoardView)'
    Set-Item -Path "$cls\$ProgId\DefaultIcon" -Value "$Icone,0"
    Set-Item -Path "$cls\$ProgId\shell\open\command" -Value ('"' + $PyW + '" "' + $Script + '" "%1"')
    foreach ($e in $Exts) {
        New-Item -Path "$cls\$e\OpenWithProgids" -Force | Out-Null
        New-ItemProperty -Path "$cls\$e\OpenWithProgids" -Name $ProgId -Value '' -PropertyType String -Force | Out-Null
        $atual = (Get-Item -Path "$cls\$e").GetValue('')
        $maquina = $null
        try { $maquina = (Get-Item -Path "Registry::HKEY_LOCAL_MACHINE\Software\Classes\$e" -ErrorAction Stop).GetValue('') } catch {}
        if ((-not $atual -or $atual -eq $ProgId) -and -not $maquina) { Set-Item -Path "$cls\$e" -Value $ProgId }
    }
    try {
        Add-Type -Namespace GDTech -Name Shell -MemberDefinition '[DllImport("shell32.dll")] public static extern void SHChangeNotify(int e, uint f, System.IntPtr a, System.IntPtr b);'
        [GDTech.Shell]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
    } catch {}
    Ok 'atalho na Área de Trabalho e no menu Iniciar'

    Remove-Item -Path $Tmp -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host ''
    Write-Host '  =========================================================' -ForegroundColor Green
    Write-Host "     PRONTO! GDTech BoardView $versao instalado." -ForegroundColor Green
    Write-Host '     Para abrir depois: atalho "GDTech BoardView" na Área de Trabalho' -ForegroundColor Green
    Write-Host '     ou no menu Iniciar. Já pode apagar a pasta do zip.' -ForegroundColor Green
    Write-Host '  =========================================================' -ForegroundColor Green
    Start-Process -FilePath $PyW -ArgumentList $arg -WorkingDirectory $Dest
    try { Stop-Transcript | Out-Null } catch {}
    exit 0
}
catch {
    Write-Host ''
    Write-Host '  =========================================================' -ForegroundColor Red
    Write-Host '     A INSTALAÇÃO NÃO TERMINOU' -ForegroundColor Red
    Write-Host '  =========================================================' -ForegroundColor Red
    Write-Host ''
    Write-Host "  Motivo: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ''
    Write-Host '  O que costuma resolver:'
    Write-Host '   1. Confira se a internet está funcionando.'
    Write-Host '   2. Rode o INSTALAR.bat de novo (ele continua de onde parou).'
    Write-Host '   3. Se o antivírus reclamou, permita e rode de novo.'
    Write-Host ''
    Write-Host "  Registro completo da instalação: $Log"
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
