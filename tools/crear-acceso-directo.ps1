<#
.SYNOPSIS
    Crea el acceso directo del panel en el Escritorio.

.DESCRIPTION
    Apunta a pythonw.exe, no a python.exe: asi el panel arranca sin
    dejar una ventana de consola negra detras.

    Las rutas se deducen de la ubicacion de este script, para que el
    acceso directo siga funcionando aunque el repositorio este clonado
    en otro sitio.

.EXAMPLE
    powershell -File tools/crear-acceso-directo.ps1
#>
[CmdletBinding()]
param(
    # Por defecto, el pythonw.exe del Python que este en el PATH.
    [string]$Pythonw,
    [string]$Nombre = 'Claude Status Widget'
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$app = Join-Path $repo 'widget\app.py'
$icono = Join-Path $repo 'assets\widget.ico'

if (-not (Test-Path $app)) {
    throw "No encuentro $app. Ejecuta el script desde el repositorio."
}

if (-not $Pythonw) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python) {
        throw 'No encuentro python en el PATH. Pasa la ruta con -Pythonw.'
    }
    $Pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
}

if (-not (Test-Path $Pythonw)) {
    throw "No encuentro pythonw.exe en $Pythonw."
}

if (-not (Test-Path $icono)) {
    Write-Warning "No existe $icono. Genera el icono con: python assets/make_icon.py"
}

$destino = Join-Path ([Environment]::GetFolderPath('Desktop')) "$Nombre.lnk"

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($destino)
$lnk.TargetPath = $Pythonw
$lnk.Arguments = '"{0}"' -f $app
$lnk.WorkingDirectory = $repo
if (Test-Path $icono) { $lnk.IconLocation = "$icono,0" }
$lnk.Description = 'Panel de estado de las sesiones de Claude Code'
$lnk.WindowStyle = 7   # minimizado: el panel es una ventana propia
$lnk.Save()

Write-Output "Acceso directo creado en: $destino"
