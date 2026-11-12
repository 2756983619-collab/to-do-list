; To-Do List 专注清单 — Inno Setup 打包脚本
; 编译：ISCC.exe ToDoList.iss

#define AppName "To-Do List 专注清单"
#define AppNameShort "To-Do List"
#define AppVersion "1.4.0"
#define AppExe "ToDoList-v" + AppVersion + ".exe"
#define AppPublisher "To-Do List"
#define SourceDir "..\dist\ToDoList-v" + AppVersion

[Setup]
AppId={{7A3F2C64-9E1B-4D58-8C2A-5B6D0E4F1A93}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\ToDoList
DefaultGroupName={#AppNameShort}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=ToDoList-Setup-v{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
AllowNoIcons=yes
CloseApplications=yes
RestartApplications=no
DisableWelcomePage=no
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} 安装程序

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："
Name: "autostart"; Description: "开机自动启动（不启动时不会监控）"; GroupDescription: "附加任务："

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; 升级时清掉旧版本的主程序（旧版叫 ToDoList.exe，新版带版本号）
Type: files; Name: "{app}\ToDoList.exe"
Type: files; Name: "{app}\ToDoList-v*.exe"

[Icons]
Name: "{autoprograms}\{#AppNameShort}"; Filename: "{app}\{#AppExe}"; Comment: "专注清单：任务管理 + 干扰源监控"
Name: "{autodesktop}\{#AppNameShort}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "ToDoList"; ValueData: """{app}\{#AppExe}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 {#AppNameShort}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 数据在 {app}\data 里：卸载只删程序文件，data 目录和里面的 todo.json 保留下来
Type: dirifempty; Name: "{app}\data"
Type: dirifempty; Name: "{app}"
