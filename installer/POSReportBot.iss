#define MyAppName "POSReportBot"
#define MyAppVersion "2.1.2"
#define MyAppPublisher "Pei Fang International"
#define MyAppExeName "POSReportBot.exe"
#define MyAppURL GetEnv("POSREPORTBOT_APP_URL")
#define PackagingExcludes "~$*;*.tmp;*.temp;*.lock;*.lck;*.bak;*.wbk;*.swp;Thumbs.db;desktop.ini"

[Setup]
AppId={{7C88E99B-0F62-4C1D-98D9-0A2F3D40B001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
#if MyAppURL != ""
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
#endif
DefaultDirName={autopf}\POSReportBot
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=POSReportBotSetup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
#if GetEnv("POSREPORTBOT_INNO_SIGNTOOL") != ""
SignTool=posreportbotsigntool
SignedUninstaller=yes
#endif

[Dirs]
Name: "C:\ProgramData\POSReportBot"
Name: "C:\ProgramData\POSReportBot\config"
Name: "C:\ProgramData\POSReportBot\downloads"
Name: "C:\ProgramData\POSReportBot\output"
Name: "C:\ProgramData\POSReportBot\logs"
Name: "C:\ProgramData\POSReportBot\screenshots"
Name: "C:\ProgramData\POSReportBot\state"
Name: "C:\ProgramData\POSReportBot\templates"

[Files]
Source: "..\dist\POSReportBot\*"; DestDir: "{app}"; Excludes: "{#PackagingExcludes}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\config_templates\app.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "app.yaml"; Flags: onlyifdoesntexist
Source: "..\config_templates\reports.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "reports.yaml"; Flags: onlyifdoesntexist
Source: "..\config_templates\branches.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "branches.yaml"; Flags: onlyifdoesntexist
Source: "..\config_templates\drive_targets.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "drive_targets.yaml"; Flags: onlyifdoesntexist
Source: "..\config_templates\reports.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "reports.template.yaml"; Flags: onlyifdoesntexist
Source: "..\config_templates\branches.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "branches.template.yaml"; Flags: onlyifdoesntexist
Source: "..\config_templates\drive_targets.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "drive_targets.template.yaml"; Flags: onlyifdoesntexist
Source: "..\config_templates\templates\*.xlsx"; DestDir: "C:\ProgramData\POSReportBot\templates"; Excludes: "{#PackagingExcludes}"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
