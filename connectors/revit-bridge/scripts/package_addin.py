"""Builds the Revit add-in package release/RevitMcpBridge-<version>.zip:

  RevitMcpBridge/
    Revit2022-2024/RevitMcpBridge.dll   (.NET Framework 4.8 build, Revit 2022 API)
    Revit2025-2026/RevitMcpBridge.dll   (.NET 8 build, Revit 2025 API)
    RevitMcpBridge.addin                (manifest copied next to each Revit version's add-ins)
    Installer.cmd / Install-RevitMcpBridge.ps1 / Uninstall-RevitMcpBridge.ps1
    LISEZMOI.txt

Run from anywhere: python package_addin.py  (needs the .NET 8 SDK)."""
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # connectors/revit-bridge
PROJECT = os.path.join(ROOT, "src", "RevitMcpBridge")
RELEASE = os.path.join(os.path.dirname(ROOT), "release")
ADDIN_ID = "8d4c1f3e-6a2b-4f7d-9e15-3b7a0c2d5e91"

version = re.search(r"<Version>([^<]+)</Version>", open(os.path.join(PROJECT, "RevitMcpBridge.csproj"), encoding="utf-8").read()).group(1)
subprocess.run(["dotnet", "build", "-c", "Release", PROJECT], check=True, stdout=subprocess.DEVNULL)

ADDIN = f"""<?xml version="1.0" encoding="utf-8"?>
<!-- Revit MCP Bridge {version}: lets Claude (Revit Dynamo Connector / Fusion Revit Dynamo ANSYS) drive this Revit. -->
<RevitAddIns>
  <AddIn Type="Application">
    <Name>Revit MCP Bridge (Claude)</Name>
    <Assembly>RevitMcpBridge\\RevitMcpBridge.dll</Assembly>
    <AddInId>{ADDIN_ID}</AddInId>
    <FullClassName>RevitMcpBridge.App.RevitApp</FullClassName>
    <VendorId>MCPB</VendorId>
    <VendorDescription>KALBEZ Ibrahim El Khalil - Claude connectors for Revit / Dynamo / ANSYS</VendorDescription>
    <Description>Local, token-protected bridge (127.0.0.1) between Revit/Dynamo and the Claude MCP connectors.</Description>
  </AddIn>
</RevitAddIns>
"""

INSTALL_PS1 = r"""<#
  Installe l'add-in Revit MCP Bridge (Claude) pour chaque Revit 2022-2026 present sur ce poste.
  Usage : clic droit > Executer avec PowerShell, ou Installer.cmd.
    -Versions 2024,2025   limiter a certaines versions
    -AllUsers             installer pour tous les utilisateurs (droits administrateur)
#>
param([int[]]$Versions, [switch]$AllUsers)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = if ($AllUsers) { Join-Path $env:ProgramData 'Autodesk\Revit\Addins' } else { Join-Path $env:APPDATA 'Autodesk\Revit\Addins' }

function Test-Revit([int]$v) {
  return (Test-Path "HKLM:\SOFTWARE\Autodesk\Revit\Autodesk Revit $v") -or
         (Test-Path (Join-Path $env:ProgramFiles "Autodesk\Revit $v\Revit.exe")) -or
         (Test-Path (Join-Path $env:ProgramData "Autodesk\Revit\Addins\$v"))
}

if (-not $Versions) { $Versions = 2022..2026 | Where-Object { Test-Revit $_ } }
if (-not $Versions) {
  Write-Host "Aucun Revit 2022-2026 detecte. Indiquez la version : .\Install-RevitMcpBridge.ps1 -Versions 2025" -ForegroundColor Yellow
  exit 1
}

foreach ($v in $Versions) {
  if ($v -lt 2022 -or $v -gt 2026) { Write-Host "Revit $v n'est pas pris en charge (2022-2026)." -ForegroundColor Yellow; continue }
  $build = if ($v -ge 2025) { 'Revit2025-2026' } else { 'Revit2022-2024' }
  $dest = Join-Path $root "$v"
  $dir = Join-Path $dest 'RevitMcpBridge'
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  Copy-Item (Join-Path $here "$build\RevitMcpBridge.dll") $dir -Force
  Copy-Item (Join-Path $here 'RevitMcpBridge.addin') $dest -Force
  Get-ChildItem $dir -Recurse | Unblock-File
  Unblock-File (Join-Path $dest 'RevitMcpBridge.addin')
  Write-Host "Revit $v : add-in installe dans $dest" -ForegroundColor Green
}
Write-Host ""
Write-Host "Redemarrez Revit. Au premier lancement, choisissez 'Toujours charger' pour 'Revit MCP Bridge (Claude)'."
Write-Host "Onglet Complements > MCP Bridge > Claude Bridge affiche l'etat de la passerelle."
"""

UNINSTALL_PS1 = r"""<# Desinstalle l'add-in Revit MCP Bridge (Claude) de toutes les versions de Revit. #>
param([switch]$AllUsers)
$root = if ($AllUsers) { Join-Path $env:ProgramData 'Autodesk\Revit\Addins' } else { Join-Path $env:APPDATA 'Autodesk\Revit\Addins' }
Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  $addin = Join-Path $_.FullName 'RevitMcpBridge.addin'
  $dir = Join-Path $_.FullName 'RevitMcpBridge'
  if (Test-Path $addin) { Remove-Item $addin -Force; Write-Host "Revit $($_.Name) : manifeste supprime" }
  if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
}
Write-Host "Termine. Fermez Revit avant de desinstaller si un fichier est verrouille."
"""

INSTALL_CMD = "@echo off\r\nrem Installe l'add-in Revit MCP Bridge pour Revit 2022-2026 (utilisateur courant).\r\npowershell -NoProfile -ExecutionPolicy Bypass -File \"%~dp0Install-RevitMcpBridge.ps1\" %*\r\npause\r\n"

README = f"""Revit MCP Bridge {version} - add-in Revit pour les connecteurs Claude
=====================================================================

Connecteurs concernes : "Revit Dynamo Connector" et "Fusion Revit Dynamo ANSYS" (Claude Desktop).
Versions de Revit : 2022, 2023, 2024 (build .NET Framework 4.8) et 2025, 2026 (build .NET 8).

INSTALLATION (2 minutes)
1. Fermez Revit.
2. Decompressez ce zip dans un dossier quelconque.
3. Double-cliquez sur Installer.cmd (ou clic droit sur Install-RevitMcpBridge.ps1 > Executer avec PowerShell).
   Le script detecte vos versions de Revit et copie l'add-in dans
   %APPDATA%\\Autodesk\\Revit\\Addins\\<annee>\\ (RevitMcpBridge.addin + dossier RevitMcpBridge).
4. Ouvrez Revit : a la question de securite sur "Revit MCP Bridge (Claude)", repondez "Toujours charger".
5. Onglet Complements > panneau MCP Bridge > "Claude Bridge" : l'etat doit indiquer
   "Running on http://127.0.0.1:8742".

Installation manuelle : copiez RevitMcpBridge.addin dans %APPDATA%\\Autodesk\\Revit\\Addins\\<annee>\\ et la DLL
de la bonne version dans le sous-dossier RevitMcpBridge\\ de ce meme dossier (clic droit > Proprietes > Debloquer).

SECURITE
- La passerelle n'ecoute que sur 127.0.0.1 (jamais sur le reseau) et exige un jeton aleatoire, ecrit dans
  %LOCALAPPDATA%\\RevitMcpBridge\\instances\\<pid>.json (lisible seulement par votre session Windows).
- Bouton "Start / Stop" : couper ou retablir l'acces de Claude a ce Revit.
- Chaque modification faite par Claude est une seule operation annulable ("Claude: ..." dans Annuler).

VARIABLES FACULTATIVES
- REVIT_MCP_PORT (port prefere, defaut 8742), REVIT_MCP_AUTOSTART=false (ne pas demarrer automatiquement).

DESINSTALLATION
- Uninstall-RevitMcpBridge.ps1 (Revit ferme).

Documentation complete : https://github.com/ibrahimkalbez/Oran-3D-Terrain-Reconstruction/blob/main/connectors/docs/REVIT.md
"""

builds = {
    "Revit2022-2024": os.path.join(PROJECT, "bin", "Release", "net48", "RevitMcpBridge.dll"),
    "Revit2025-2026": os.path.join(PROJECT, "bin", "Release", "net8.0-windows", "RevitMcpBridge.dll"),
}
os.makedirs(RELEASE, exist_ok=True)
out = os.path.join(RELEASE, f"RevitMcpBridge-{version}.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for folder, dll in builds.items():
        z.write(dll, f"RevitMcpBridge/{folder}/RevitMcpBridge.dll")
    z.writestr("RevitMcpBridge/RevitMcpBridge.addin", ADDIN.replace("\n", "\r\n"))
    # PowerShell 5.1 reads BOM-less scripts as ANSI: the scripts are ASCII, written with CRLF.
    z.writestr("RevitMcpBridge/Install-RevitMcpBridge.ps1", INSTALL_PS1.replace("\n", "\r\n"))
    z.writestr("RevitMcpBridge/Uninstall-RevitMcpBridge.ps1", UNINSTALL_PS1.replace("\n", "\r\n"))
    z.writestr("RevitMcpBridge/Installer.cmd", INSTALL_CMD)
    z.writestr("RevitMcpBridge/LISEZMOI.txt", README.replace("\n", "\r\n"))

for text in (INSTALL_PS1, UNINSTALL_PS1, INSTALL_CMD, README):
    text.encode("ascii")  # scripts and readme must stay ASCII for Windows PowerShell / Notepad
names = zipfile.ZipFile(out).namelist()
print(f"{out}\n" + "\n".join("  " + n for n in names))
