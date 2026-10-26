; Instalador do GDTech BoardView (Inno Setup 6) — gerado pelo build_installer.bat
; Não precisa de administrador: instala só para o usuário (ou para todos, se escolher).

#ifndef MyAppVersion
  #define MyAppVersion "0.0"
#endif
#define MyAppName "GDTech BoardView"
#define MyAppExe "GDTech_BoardView.exe"
#define MyProgId "GDTechBoardView.Boardview"

[Setup]
AppId={{6F2C7A31-9D4E-4B8A-B7E2-5C1D0A9F3E21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=GDTech Informática
AppPublisherURL=https://youtube.com/GDTechinformatica
DefaultDirName={autopf}\GDTech BoardView
DefaultGroupName=GDTech BoardView
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=GDTech_BoardView_Setup_{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ChangesAssociations=yes
UninstallDisplayIcon={app}\{#MyAppExe}
SetupIconFile=..\gdtech_boardview.ico

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"
Name: "assoc"; Description: "Abrir boardviews (.tvw, .fz, .bdv, .bvr, .cae, .cst, .bv…) com o GDTech BoardView"; GroupDescription: "Arquivos:"
Name: "openwith"; Description: "Mostrar no ""Abrir com"" também para .brd, .cad, .pcb, .asc (sem trocar o programa padrão)"; GroupDescription: "Arquivos:"

[Files]
Source: "..\dist\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; tipo de arquivo
Root: HKA; Subkey: "Software\Classes\{#MyProgId}"; ValueType: string; ValueData: "Boardview"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#MyProgId}\DefaultIcon"; ValueType: string; ValueData: "{app}\{#MyAppExe},0"
Root: HKA; Subkey: "Software\Classes\{#MyProgId}\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExe}"" ""%1"""
; programa padrão (formatos que só boardview usa)
#define Assoc(Ext) 'Root: HKA; Subkey: "Software\Classes\' + Ext + '"; ValueType: string; ValueName: ""; ValueData: "' + MyProgId + '"; Flags: uninsdeletevalue; Tasks: assoc' + NewLine + 'Root: HKA; Subkey: "Software\Classes\' + Ext + '\OpenWithProgids"; ValueType: string; ValueName: "' + MyProgId + '"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc'
#emit Assoc(".tvw")
#emit Assoc(".fz")
#emit Assoc(".bdv")
#emit Assoc(".bvr")
#emit Assoc(".cae")
#emit Assoc(".cst")
#emit Assoc(".bv")
#emit Assoc(".faz")
#emit Assoc(".f2b")
#emit Assoc(".gr")
#emit Assoc(".a3p")
#emit Assoc(".nbv")
; só no "Abrir com" (essas extensões também são de outros programas)
#define OpenWith(Ext) 'Root: HKA; Subkey: "Software\Classes\' + Ext + '\OpenWithProgids"; ValueType: string; ValueName: "' + MyProgId + '"; ValueData: ""; Flags: uninsdeletevalue; Tasks: openwith'
#emit OpenWith(".brd")
#emit OpenWith(".cad")
#emit OpenWith(".pcb")
#emit OpenWith(".asc")

[Run]
; abre o programa no fim da instalação
Filename: "{app}\{#MyAppExe}"; Description: "Abrir o GDTech BoardView"; Flags: nowait postinstall
