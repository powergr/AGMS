; Inno Setup script for AGMS.
; Build it with:  ISCC /DAppVersion=1.3.2 installer\agms.iss
; build_windows.ps1 does this for you, after it builds the app into dist\AGMS.

#ifndef AppVersion
  #error Pass the version on the command line, for example: ISCC /DAppVersion=1.3.2 installer\agms.iss
#endif

#define AppName "Another Google Maps Scraper (AGMS)"
#define AppExe "AGMS.exe"

[Setup]
; Never change this ID. It is how a newer setup file recognises and upgrades an existing install.
AppId={{042320B2-9C45-41AB-9D9E-D3BB2E335834}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Pashalis Laoutaris
AppPublisherURL=https://github.com/powergr/AGMS
AppSupportURL=https://github.com/powergr/AGMS/issues
AppUpdatesURL=https://github.com/powergr/AGMS/releases
DefaultDirName={autopf}\AGMS
DisableProgramGroupPage=yes
; Installs for the current user without an admin prompt. The wizard offers all users as a choice.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=AGMS-Setup-{#AppVersion}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\AGMS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Icons]
Name: "{autoprograms}\AGMS"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\AGMS"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start AGMS now"; Flags: nowait postinstall skipifsilent

; Results are saved in Documents\AGMS, which uninstalling deliberately leaves alone.

[Code]
function ChromeInstalled: Boolean;
var
  Path: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe', '', Path) or
    RegQueryStringValue(HKLM32, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe', '', Path) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe', '', Path);
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if not ChromeInstalled then
    MsgBox('AGMS drives Google Chrome, and Chrome was not found on this computer.' + #13#10 +
           'You can finish this setup now, but install Chrome before you start scraping.',
           mbInformation, MB_OK);
end;
