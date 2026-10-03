<#
  PortableTAK Setup for Windows: make a PortableTAK USB drive on this computer.

  Asks for the server name and passwords (or makes a blank drive), lets you
  pick the USB drive (external USB disks only), then builds and writes the
  drive inside the PortableTAK WSL host and verifies it. Same result as
  PortableTAK Setup on a Mac. Needs Windows 11 and administrator rights.
#>
$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot
$StartHere = Join-Path $Here 'start-here'

# Reuse the PortableTAK launcher's WSL helpers (Say, Fail, Ensure-Wsl, Ensure-Distro, ...).
$env:PORTABLETAK_LIBRARY = '1'
. (Join-Path $StartHere '.portabletak\windows\PortableTAK.ps1')
$env:PORTABLETAK_LIBRARY = $null
$Action = 'setup'

if (-not (Test-Admin)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
    exit 0
}

function Pick-Disk {
    $disks = @(Get-Disk | Where-Object { $_.BusType -eq 'USB' -and -not $_.IsBoot -and -not $_.IsSystem } | Sort-Object Number)
    if ($disks.Count -eq 0) { Fail 'No USB drive found. Plug in the drive (16 GB or larger) and run setup again.' }
    Say 'USB drives on this computer:' 'Cyan'
    for ($i = 0; $i -lt $disks.Count; $i++) {
        Say ("  {0}) {1}  {2:N0} GB  (disk {3})" -f ($i + 1), $disks[$i].FriendlyName, ($disks[$i].Size / 1GB), $disks[$i].Number)
    }
    while ($true) {
        $pick = Read-Host 'Number of the drive to ERASE and use for PortableTAK'
        if ($pick -match '^\d+$' -and [int]$pick -ge 1 -and [int]$pick -le $disks.Count) { break }
        Say '  Pick a number from the list.' 'Yellow'
    }
    $disk = $disks[[int]$pick - 1]
    if ($disk.Size -lt 16GB) { Fail "$($disk.FriendlyName) is smaller than 16 GB." }
    if ($disk.Size -gt 2TB) { Fail "$($disk.FriendlyName) is larger than 2 TB; refusing." }
    $phrase = "ERASE DISK $($disk.Number)"
    for ($try = 1; $try -le 3; $try++) {
        $answer = Read-Host "Type  $phrase  (exactly, or 'cancel') to continue"
        if ($answer -ceq $phrase) { return $disk }
        if ($answer -eq 'cancel') { Fail 'Cancelled; nothing was changed.' }
        Say "  Type the whole phrase:  $phrase" 'Yellow'
    }
    Fail 'Not confirmed; nothing was changed.'
}

function Read-Answers {
    Say ''
    Say 'What do you want to make?' 'Cyan'
    Say '  1) A ready-to-use drive (set it up now: name and passwords)   [recommended]'
    Say '  2) A blank drive (it asks for name and passwords the first time it starts on Windows)'
    while ($true) {
        $mode = Read-Host 'Choose 1 or 2 [1]'
        if (-not $mode) { $mode = '1' }
        if ($mode -in @('1', '2')) { break }
        Say '  Type 1 or 2.' 'Yellow'
    }
    if ($mode -eq '2') { return $null }
    while ($true) {
        $name = (Read-Host 'Server name (letters, numbers, space . _ -; e.g. Team Alpha TAK)').Trim()
        if ($name -match '^[A-Za-z0-9]([A-Za-z0-9 ._-]{0,62}[A-Za-z0-9])?$') { break }
        Say "  That name can't be used. Try again." 'Yellow'
    }
    while ($true) {
        $admin = Read-NewPassword 'Administrator password (dashboard sign-in)'
        $client = Read-NewPassword 'Client password (used when phones enroll)'
        if ($admin -ne $client) { break }
        Say '  The two passwords must be different. Try again.' 'Yellow'
    }
    return (@{ instance_name = $name; administrator_password = $admin; client_password = $client } | ConvertTo-Json -Compress)
}

# --- main ----------------------------------------------------------------------------
Say 'PortableTAK Setup for Windows' 'Cyan'
$build = Test-Windows
if ($build -lt 22000) { Fail 'Making drives needs Windows 11.' }
Ensure-Wsl
Ensure-Distro $StartHere
$answers = Read-Answers
$disk = Pick-Disk

Say 'Preparing (copying setup files into the PortableTAK host)...'
Invoke-Distro 'rm -rf /var/tmp/pt-setup && mkdir -p /var/tmp/pt-setup/secrets && chmod 700 /var/tmp/pt-setup/secrets' | Out-Null
$share = "\\wsl.localhost\$Distro\var\tmp\pt-setup"
Copy-Item -Path (Join-Path $Here 'clean-base') -Destination $share -Recurse -Force
Copy-Item -Path $StartHere -Destination $share -Recurse -Force
Copy-Item -Path (Join-Path $Here 'container-setup.sh') -Destination $share -Force
$mode = 'blank'
if ($answers) {
    [IO.File]::WriteAllText("$share\secrets\request.json", $answers, (New-Object Text.UTF8Encoding($false)))
    $answers = $null
    $mode = ''
}
# Downloaded files carry Windows' Zone.Identifier stream, which arrives as extra
# 'name:Zone.Identifier' files (a ':' is not allowed on the FAT32 Start Here partition).
Invoke-Distro "find /var/tmp/pt-setup -name '*:Zone.Identifier' -delete" -AllowFail | Out-Null
Invoke-Distro 'chmod 600 /var/tmp/pt-setup/secrets/* 2>/dev/null; sed -i "s/\r$//" /var/tmp/pt-setup/container-setup.sh' -AllowFail | Out-Null

# The drive is erased anyway: wipe it on the Windows side first, so Windows has no
# volume to keep open (Explorer, indexing or antivirus would block the handover).
Say "Clearing $($disk.FriendlyName)..."
try { Clear-Disk -Number $disk.Number -RemoveData -RemoveOEM -Confirm:$false -ErrorAction Stop } catch { Log ("Clear-Disk: " + $_) }
Update-HostStorageCache -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Say "Attaching $($disk.FriendlyName) to the PortableTAK host..."
$record = Attach-Disk $disk.Number
try {
    Say 'Building the drive (about 15-25 minutes; progress shows at the top of this window)...'
    $result = Invoke-DistroLive "/usr/local/portabletak/bin/portabletak-wsl-make-drive $([int64]$disk.Size) $mode" 'Building the drive' {
        param($line)
        # Leading `r: WSL leaves the console without carriage return on newline.
        if ($line -match '^PORTABLETAK_(MAKE_STEP|SETUP_STEP)=(.*)') { Say ("`r  step: " + $Matches[2]) }
        elseif ($line -match '^PORTABLETAK_SETUP_(SERVICES|ICONS)=') { Say ("`r  " + $line.Substring(18)) }
    }
    $out = $result.Out
} finally {
    Detach-Disk $record
    Invoke-Distro 'rm -rf /var/tmp/pt-setup' -AllowFail | Out-Null
}
if ($out -notmatch 'PORTABLETAK_MAKE_DRIVE=COMPLETE') {
    Fail ('Making the drive failed. ' + (($out -split "`n" | Where-Object { $_ -match '_ERROR=|REFUSED' }) -join ' '))
}
Say ''
if ($mode -eq 'blank') {
    Say 'The blank PortableTAK drive is ready. The first time it starts on Windows, it asks for the name and passwords.' 'Green'
} else {
    Say 'The PortableTAK drive is ready.' 'Green'
}
Say 'Unplug it and use it in an OpenMANET node, or double-click "Start PortableTAK (Windows)" on it.' 'Green'
Read-Host 'Press Enter to close' | Out-Null
