#define MyAppName "POSReportBot"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "POSReportBot"
#define MyAppExeName "POSReportBot.exe"

[Setup]
AppId={{7C88E99B-0F62-4C1D-98D9-0A2F3D40B001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\POSReportBot
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=POSReportBotSetup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Dirs]
Name: "C:\ProgramData\POSReportBot"
Name: "C:\ProgramData\POSReportBot\config"
Name: "C:\ProgramData\POSReportBot\downloads"
Name: "C:\ProgramData\POSReportBot\output"
Name: "C:\ProgramData\POSReportBot\logs"
Name: "C:\ProgramData\POSReportBot\screenshots"
Name: "C:\ProgramData\POSReportBot\state"

[Files]
Source: "..\dist\POSReportBot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\config_templates\app.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "app.yaml"; Flags: onlyifdoesntexist
Source: "..\config_templates\reports.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "reports.yaml"; Flags: onlyifdoesntexist
Source: "..\config_templates\branches.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "branches.yaml"; Flags: onlyifdoesntexist
Source: "..\config_templates\drive_targets.template.yaml"; DestDir: "C:\ProgramData\POSReportBot\config"; DestName: "drive_targets.yaml"; Flags: onlyifdoesntexist

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
