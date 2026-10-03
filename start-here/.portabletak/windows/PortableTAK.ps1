<#
  PortableTAK for Windows: run the PortableTAK server on this computer from the
  PortableTAK USB drive, using WSL2 (Windows Subsystem for Linux).

    PortableTAK.ps1 start            start the server from the plugged-in drive, open the dashboard
    PortableTAK.ps1 stop             stop the server; the drive can then be removed
    PortableTAK.ps1 status
    PortableTAK.ps1 install-helper   start automatically whenever the drive is plugged in
    PortableTAK.ps1 remove-helper
    PortableTAK.ps1 watch            (used by the helper)

  Needs Windows 11 (or Windows 10 22H2 with the Microsoft Store version of WSL)
  and administrator rights (Windows asks). Phones reach the server at this
  computer's Wi-Fi/Ethernet address; WinTAK on this computer can use the same.
#>
param([Parameter(Position = 0)][string]$Action = 'start', [string]$DriveLetter = '')

$ErrorActionPreference = 'Stop'
$Distro = 'PortableTAK'
$Home2 = Join-Path $env:LOCALAPPDATA 'PortableTAK'
$StateFile = Join-Path $Home2 'attached-disk.txt'
$LogFile = Join-Path $Home2 'portabletak.log'
# TAK (CoT, web, dashboard, enrollment) and video (RTSP, RTMP, HLS, WebRTC, SRT).
$TakPorts = 8089, 8443, 8444, 8446, 8554, 1935, 8888, 8889, 8890
$TakUdpPorts = 8000, 8001, 8189, 8890
$VmCreatorId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'   # WSL's Hyper-V firewall identity

function Say([string]$Text, [string]$Color = 'Gray') { Write-Host $Text -ForegroundColor $Color; Log $Text }
function Log([string]$Text) {
    try { New-Item -ItemType Directory -Force -Path $Home2 | Out-Null
          Add-Content -Path $LogFile -Value ("{0:u} {1}" -f (Get-Date), $Text) } catch { }
}
function Fail([string]$Text) {
    Say "PortableTAK: $Text" 'Red'
    Say "Details are in $LogFile" 'DarkGray'
    if ($Host.Name -eq 'ConsoleHost' -and $Action -ne 'watch') { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
}

# Any unexpected error: show it and keep the window open instead of closing.
trap {
    Say ("PortableTAK: unexpected error: " + $_.Exception.Message) 'Red'
    Say "Details are in $LogFile" 'DarkGray'
    Log ($_ | Out-String)
    if ($Host.Name -eq 'ConsoleHost' -and $Action -ne 'watch') { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
}

function Test-Admin {
    $id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Windows PowerShell 5.1 turns a native program's error output into an error record, and
# with ErrorActionPreference = Stop that ends the script (e.g. "WSL is not installed").
# Native calls run with Continue and their error lines are kept as plain text.
function Invoke-Wsl([string[]]$Arguments, [switch]$AllowFail) {
    $ErrorActionPreference = 'Continue'
    # wsl.exe prints UTF-16; read it as such.
    $old = [Console]::OutputEncoding
    [Console]::OutputEncoding = [Text.Encoding]::Unicode
    try { $out = & wsl.exe @Arguments 2>&1 | ForEach-Object { "$_" } | Out-String } finally { [Console]::OutputEncoding = $old }
    $code = $LASTEXITCODE
    Log ("wsl {0} -> {1}: {2}" -f ($Arguments -join ' '), $code, $out.Trim())
    if ($code -ne 0 -and -not $AllowFail) { Fail ("wsl {0} failed: {1}" -f ($Arguments -join ' '), $out.Trim()) }
    return @{ Code = $code; Out = $out }
}

function Invoke-Distro([string]$Command, [switch]$AllowFail) {
    # Plain output (UTF-8) from inside the PortableTAK distribution, as root.
    # wsl.exe hands its arguments to a second Linux shell, which would expand $, quotes
    # and loops too early; the command travels base64-encoded and is decoded inside.
    $ErrorActionPreference = 'Continue'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Command))
    $out = & wsl.exe -d $Distro -u root -- /bin/sh -c "echo $encoded | base64 -d | /bin/sh" 2>&1 | ForEach-Object { "$_" } | Out-String
    $code = $LASTEXITCODE
    Log ("[$Distro] $Command -> $code`n" + $out.Trim())
    if ($code -ne 0 -and -not $AllowFail) { Fail "PortableTAK command failed ($Command). $($out.Trim())" }
    return @{ Code = $code; Out = $out }
}

function Invoke-DistroLive([string]$Command, [string]$Activity, [scriptblock]$OnLine) {
    # Invoke-Distro for long jobs: a progress bar shows the current step, the minutes
    # so far and, while the drive is written or checked, the GB done. Linux reports
    # every 5 seconds (PORTABLETAK_TICK=<sectors read> <sectors written> of the USB drive).
    $ticker = '( while sleep 5; do d=$(lsblk -dno NAME,TRAN 2>/dev/null | awk ''$2=="usb"{print $1; exit}''); ' +
              'if [ -n "$d" ]; then set -- $(cat /sys/block/$d/stat); echo "PORTABLETAK_TICK=$3 $7"; ' +
              'else echo "PORTABLETAK_TICK=0 0"; fi; done ) & t=$!; '
    $full = $ticker + $Command + '; r=$?; kill $t 2>/dev/null; exit $r'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($full))
    $state = @{ Step = 'starting'; Base = $null; Start = Get-Date }
    $lines = New-Object System.Collections.Generic.List[string]
    $ErrorActionPreference = 'Continue'
    & wsl.exe -d $Distro -u root -- /bin/sh -c "echo $encoded | base64 -d | /bin/sh" 2>&1 | ForEach-Object {
        $line = "$_"
        if ($line -match '^PORTABLETAK_TICK=(\d+) (\d+)') {
            $read = [int64]$Matches[1]; $written = [int64]$Matches[2]
            if (-not $state.Base) { $state.Base = @($read, $written) }
            $detail = ''
            if ($state.Step -eq 'write') { $detail = ' - {0:N1} GB written' -f (($written - $state.Base[1]) * 512 / 1e9) }
            elseif ($state.Step -eq 'verify') { $detail = ' - {0:N1} GB checked' -f (($read - $state.Base[0]) * 512 / 1e9) }
            $minutes = [int][math]::Floor(((Get-Date) - $state.Start).TotalMinutes)
            Write-Progress -Activity $Activity -Status ('{0}{1}  ({2} min so far)' -f ($state.Step -replace '_', ' '), $detail, $minutes)
            return
        }
        if ($line -match '^PORTABLETAK_(MAKE_STEP|SETUP_STEP|SETUP_PHASE)=(.*)') { $state.Step = $Matches[2].Trim(); $state.Base = $null }
        $lines.Add($line)
        if ($OnLine) { & $OnLine $line }
    }
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    Write-Progress -Activity $Activity -Completed
    $out = $lines -join "`n"
    Log ("[$Distro] $Command -> $code`n" + $out.Trim())
    return @{ Code = $code; Out = $out }
}

# --- locate the PortableTAK drive -------------------------------------------------
function Restore-DriveLetter {
    # Starting the server removes the drive's letter (so Windows cannot hold it);
    # give it back: partition 1 FAT32 (Start here) next to partition 2 Linux.
    foreach ($disk in @(Get-Disk -ErrorAction SilentlyContinue | Where-Object { $_.BusType -eq 'USB' -and -not $_.IsBoot -and -not $_.IsSystem })) {
        $parts = @(Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue)
        $start = $parts | Where-Object { $_.PartitionNumber -eq 1 -and $_.MbrType -eq 12 }
        $linux = $parts | Where-Object { $_.PartitionNumber -eq 2 -and $_.MbrType -eq 131 }
        if ($start -and $linux -and -not ("$($start.DriveLetter)" -match '^[A-Za-z]$')) {
            try {
                $start | Add-PartitionAccessPath -AssignDriveLetter -ErrorAction Stop
                Log "gave the drive letter back (disk $($disk.Number))"
            } catch { Log ("drive letter: " + $_) }
        }
    }
}

function Find-Drive {
    Restore-DriveLetter
    $volumes = Get-Volume | Where-Object { $_.FileSystemLabel -eq 'PORTABLETAK' -and $_.DriveLetter }
    if ($DriveLetter) { $volumes = $volumes | Where-Object { $_.DriveLetter -eq $DriveLetter[0] } }
    foreach ($v in $volumes) {
        $root = "$($v.DriveLetter):\"
        if (-not (Test-Path (Join-Path $root '.portabletak\windows\PortableTAK.ps1'))) { continue }
        $part = Get-Partition -DriveLetter $v.DriveLetter
        $disk = $part | Get-Disk
        $parts = Get-Partition -DiskNumber $disk.Number
        if (-not ($parts | Where-Object { $_.PartitionNumber -eq 2 })) { continue }
        if ($disk.BusType -notin @('USB', 'SD', 'MMC', 'SCSI', 'SATA', 'NVMe')) { continue }
        if ($disk.IsBoot -or $disk.IsSystem) { continue }   # never the Windows disk
        return @{ Root = $root; Disk = $disk.Number; Name = $disk.FriendlyName; Size = $disk.Size }
    }
    return $null
}

# --- one-time preparation of this computer ----------------------------------------
function Clean-Drive([string]$Root) {
    # Remove macOS leftovers ("._name", .DS_Store) and hide macOS housekeeping folders.
    Get-ChildItem -Path $Root -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like '._*' -or $_.Name -eq '.DS_Store' } | Remove-Item -Force -ErrorAction SilentlyContinue
    foreach ($name in '.fseventsd', '.metadata_never_index', '.Spotlight-V100', '.Trashes', '.portabletak') {
        $item = Get-Item -Path (Join-Path $Root $name) -Force -ErrorAction SilentlyContinue
        if ($item) { $item.Attributes = $item.Attributes -bor [IO.FileAttributes]::Hidden }
    }
}

function Copy-Tools([string]$From) {
    Clean-Drive $From
    # Attaching the drive to WSL takes it away from Windows, so run from a local copy.
    New-Item -ItemType Directory -Force -Path $Home2 | Out-Null
    $src = Join-Path $From '.portabletak\windows'
    Copy-Item -Path (Join-Path $src '*') -Destination $Home2 -Recurse -Force
    foreach ($guide in Get-ChildItem -Path $From -Filter '*.pdf' -ErrorAction SilentlyContinue) {
        Copy-Item $guide.FullName -Destination $Home2 -Force
    }
}

function Test-Windows {
    $build = [Environment]::OSVersion.Version.Build
    if ($build -lt 19045) { Fail 'This Windows version is too old. PortableTAK needs Windows 11 or Windows 10 22H2.' }
    return $build
}

function Ensure-Wsl {
    $status = Invoke-Wsl @('--status') -AllowFail
    if ($status.Code -ne 0 -or $status.Out -match 'not installed|wsl --install') {
        Say 'Windows Subsystem for Linux (WSL) is needed and is not installed yet.' 'Yellow'
        $answer = Read-Host 'Install it now? A restart is needed afterwards (Y/N)'
        if ($answer -notmatch '^[Yy]') { Fail 'WSL is required. Run this again when you are ready.' }
        Say 'Installing WSL (downloads from Microsoft; a few minutes)...'
        $ErrorActionPreference = 'Continue'
        & wsl.exe --install --no-distribution
        $installed = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($installed -ne 0) { Fail "Installing WSL failed (code $installed). Check the internet connection and try again, or run 'wsl --install' in an administrator PowerShell." }
        Say 'WSL is installed. Restart Windows, then double-click "Start PortableTAK (Windows)" again.' 'Yellow'
        Read-Host 'Press Enter to close' | Out-Null
        exit 0
    }
    $version = Invoke-Wsl @('--version') -AllowFail
    if ($version.Code -ne 0) {
        Say 'Updating WSL (needed to attach USB drives)...'
        Invoke-Wsl @('--update') | Out-Null
    }
}

function Ensure-Networking([int]$Build) {
    # Mirrored networking (Windows 11 22H2+): WSL shares this computer's addresses,
    # so phones reach PortableTAK at the laptop's own Wi-Fi/Ethernet address.
    if ($Build -lt 22621) { Fail 'Phones can only reach PortableTAK on Windows 11 22H2 or newer (mirrored networking).' }
    $config = Join-Path $env:USERPROFILE '.wslconfig'
    $text = if (Test-Path $config) { Get-Content $config -Raw } else { '' }
    if ($text -notmatch '(?im)^\s*networkingMode\s*=\s*mirrored') {
        if ($text -match '(?im)^\s*networkingMode\s*=') {
            $text = $text -replace '(?im)^\s*networkingMode\s*=.*$', 'networkingMode=mirrored'
        } elseif ($text -match '(?im)^\s*\[wsl2\]') {
            $text = $text -replace '(?im)^\s*\[wsl2\]\s*$', "[wsl2]`r`nnetworkingMode=mirrored"
        } else {
            $text = $text.TrimEnd() + "`r`n[wsl2]`r`nnetworkingMode=mirrored`r`n"
        }
        if (Test-Path $config) { Copy-Item $config "$config.before-portabletak" -Force }
        Set-Content -Path $config -Value $text -Encoding ASCII
        Say 'Turned on WSL mirrored networking (so phones can reach this computer).'
        Invoke-Wsl @('--shutdown') -AllowFail | Out-Null
    }
    $ports = ($TakPorts | ForEach-Object { "$_" })
    $udpPorts = ($TakUdpPorts | ForEach-Object { "$_" })
    try {
        # Mirrored networking: the Hyper-V firewall blocks inbound by default, and on some
        # laptops port rules alone are not honoured; allow inbound to the WSL VM (Microsoft's
        # documented setting). Only PortableTAK's services listen on the network inside it.
        $vm = Get-NetFirewallHyperVVMSetting -PolicyStore ActiveStore -Name $VmCreatorId -ErrorAction SilentlyContinue
        if ($vm -and $vm.DefaultInboundAction -ne 'Allow') {
            Set-NetFirewallHyperVVMSetting -Name $VmCreatorId -DefaultInboundAction Allow
            Say 'Allowed incoming connections to PortableTAK (WSL firewall).'
        }
        if (-not (Get-NetFirewallHyperVRule -Name 'PortableTAK-TAK' -ErrorAction SilentlyContinue)) {
            New-NetFirewallHyperVRule -Name 'PortableTAK-TAK' -DisplayName 'PortableTAK (TAK clients)' `
                -Direction Inbound -VMCreatorId $VmCreatorId -Protocol TCP -LocalPorts $ports | Out-Null
            New-NetFirewallHyperVRule -Name 'PortableTAK-TAK-UDP' -DisplayName 'PortableTAK (TAK clients, UDP)' `
                -Direction Inbound -VMCreatorId $VmCreatorId -Protocol UDP -LocalPorts $udpPorts | Out-Null
            Say 'Allowed TAK client connections through the Windows firewall.'
        }
    } catch { Log "Hyper-V firewall rule: $_" }
    if (-not (Get-NetFirewallRule -Name 'PortableTAK-TAK' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'PortableTAK-TAK' -DisplayName 'PortableTAK (TAK clients)' -Direction Inbound `
            -Protocol TCP -LocalPort $ports -Action Allow -Profile Any | Out-Null
        New-NetFirewallRule -Name 'PortableTAK-TAK-UDP' -DisplayName 'PortableTAK (TAK clients, UDP)' -Direction Inbound `
            -Protocol UDP -LocalPort $udpPorts -Action Allow -Profile Any | Out-Null
    }
}

function Ensure-Distro([string]$From) {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $tarball = Join-Path $From ".portabletak\wsl\portabletak-wsl-$arch.tar.gz"
    if (-not (Test-Path $tarball)) { Fail "The drive is missing its Windows host files ($tarball)." }
    $wanted = (Get-Content "$tarball.sha256" -ErrorAction SilentlyContinue | Select-Object -First 1)
    $marker = Join-Path $Home2 'distro.sha256'
    $list = Invoke-Wsl @('--list', '--quiet') -AllowFail
    $present = ($list.Out -split "`r?`n" | ForEach-Object { $_.Trim([char]0, ' ') }) -contains $Distro
    $current = if (Test-Path $marker) { Get-Content $marker | Select-Object -First 1 } else { '' }
    if ($present -and $wanted -and $current -eq $wanted) { return }
    if ($present) {
        Say 'Updating the PortableTAK host on this computer...'
        Invoke-Wsl @('--unregister', $Distro) | Out-Null
    } else {
        Say 'Installing the PortableTAK host on this computer (one time, about a minute)...'
    }
    $target = Join-Path $Home2 'wsl'
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Invoke-Wsl @('--import', $Distro, $target, $tarball, '--version', '2') | Out-Null
    if ($wanted) { Set-Content -Path $marker -Value $wanted }
}

function Get-ServiceAddress {
    # The address phones use: Wi-Fi first, then Ethernet. A node's Wi-Fi has no gateway
    # while the node has no internet, so a gateway is preferred but not required.
    # VPN adapters (the Tailscale app, 100.64.0.0/10), virtual and link-local addresses
    # are never used: phones on the local network cannot reach them.
    $candidates = Get-NetIPConfiguration | Where-Object {
        $_.NetAdapter.Status -eq 'Up' -and $_.IPv4Address -and
        $_.InterfaceAlias -notmatch 'vEthernet|Loopback|Tailscale|ZeroTier|VPN|WireGuard|OpenVPN|Bluetooth' -and
        $_.NetAdapter.InterfaceDescription -notmatch 'Tailscale|Wintun|WireGuard|TAP-|Hyper-V|Virtual' }
    $candidates = $candidates | Where-Object {
        $ip = ($_.IPv4Address | Select-Object -First 1).IPAddress
        $ip -notmatch '^169\.254\.' -and $ip -notmatch '^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.' }
    $sorted = @($candidates | Sort-Object { if ($_.InterfaceAlias -match 'Wi-?Fi|Wireless|WLAN') { 0 }
                                             elseif ($_.InterfaceAlias -match 'Ethernet') { 1 } else { 2 } },
                                           { if ($_.IPv4DefaultGateway) { 0 } else { 1 } })
    if (-not $sorted) { Fail 'This computer is not connected to a network. Connect to Wi-Fi or Ethernet first.' }
    # First: the address phones normally use. All of them go on the server certificate,
    # so a phone on any network this computer is on (Wi-Fi and Ethernet) can connect.
    return @($sorted | ForEach-Object { ($_.IPv4Address | Select-Object -First 1).IPAddress } | Select-Object -Unique)
}

# --- attaching the USB drive to PortableTAK (usbipd) -------------------------------
# Windows marks USB flash drives as removable media, which `wsl --mount` cannot attach.
# usbipd-win (the USB passthrough tool Microsoft documents for WSL) hands the whole USB
# device to PortableTAK's Linux side instead, where it appears as a normal USB disk.
function Get-Usbipd {
    $cmd = Get-Command usbipd.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $path = Join-Path $env:ProgramFiles 'usbipd-win\usbipd.exe'
    if (Test-Path $path) { return $path }
    return $null
}

function Ensure-Usbipd {
    if (Get-Usbipd) { return }
    Say 'Installing usbipd (lets PortableTAK use USB drives; one time)...'
    # The drive carries the installer for both processor types, so no internet is needed.
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
    $msi = Get-ChildItem -Path $PSScriptRoot -Filter "usbipd-win_*_$arch.msi" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
    $ErrorActionPreference = 'Continue'
    if ($msi) {
        Start-Process msiexec.exe -ArgumentList @('/i', "`"$($msi.FullName)`"", '/qn', '/norestart') -Wait
    } elseif (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        & winget.exe install --exact --id dorssel.usbipd-win --silent --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | ForEach-Object { Log "$_" }
    }
    $ErrorActionPreference = 'Stop'
    if (-not (Get-Usbipd)) { Fail 'Could not install usbipd. Connect to the internet and try again, or install it from https://github.com/dorssel/usbipd-win/releases.' }
}

function Invoke-Usbipd([string[]]$Arguments, [switch]$AllowFail) {
    $ErrorActionPreference = 'Continue'
    $out = & (Get-Usbipd) @Arguments 2>&1 | ForEach-Object { "$_" } | Out-String
    $code = $LASTEXITCODE
    Log ("usbipd {0} -> {1}: {2}" -f ($Arguments -join ' '), $code, $out.Trim())
    if ($code -ne 0 -and -not $AllowFail) { Fail ("usbipd {0} failed: {1}" -f ($Arguments -join ' '), $out.Trim()) }
    return @{ Code = $code; Out = $out }
}

function Get-DiskBusId([int]$DiskNumber) {
    # The disk's Plug and Play device, then up its parents to the USB device usbipd lists.
    $devices = @((Invoke-Usbipd @('state')).Out | ConvertFrom-Json | Select-Object -ExpandProperty Devices |
                 Where-Object { $_.BusId })
    $byId = @{}
    foreach ($d in $devices) { $byId[$d.InstanceId.ToUpperInvariant()] = $d.BusId }
    $disk = Get-Disk -Number $DiskNumber
    $id = (($disk.Path -replace '^\\\\\?\\', '') -replace '#\{[^}]+\}$', '') -replace '#', '\'
    for ($i = 0; $i -lt 8 -and $id; $i++) {
        if ($byId.ContainsKey($id.ToUpperInvariant())) { return $byId[$id.ToUpperInvariant()] }
        $id = (Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
    }
    # Fallback: the only USB mass-storage device attached.
    $storage = @($devices | Where-Object { $_.Description -match 'Mass Storage|USB Attached SCSI' })
    if ($storage.Count -eq 1) { return $storage[0].BusId }
    return $null
}

function Dismount-DiskVolumes([int]$DiskNumber) {
    # Force-dismount every volume on the disk, with or without a drive letter.
    $paths = @(Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue | ForEach-Object { $_.AccessPaths } |
               Where-Object { $_ -like '\\?\Volume*' })
    foreach ($vol in @(Get-CimInstance Win32_Volume -ErrorAction SilentlyContinue | Where-Object { $_.DeviceID -in $paths })) {
        $r = Invoke-CimMethod -InputObject $vol -MethodName Dismount -Arguments @{Force = $true; Permanent = $false} -ErrorAction SilentlyContinue
        Log ("dismount $($vol.DeviceID) -> " + $r.ReturnValue)
    }
}

function Stop-WslFileHelper {
    # WSL's Windows-side file sharing (Plan9FileSystem, a dllhost.exe) can keep a USB
    # drive open even after "wsl --shutdown" (microsoft/WSL#7972), which stops the
    # drive from being handed to PortableTAK. WSL starts it again when needed.
    $helpers = @(Get-CimInstance Win32_Process -Filter "Name='dllhost.exe'" -ErrorAction SilentlyContinue |
                 Where-Object { $_.CommandLine -match 'DFB65C4C-B34F-435D-AFE9-A86218684AA8' })
    foreach ($h in $helpers) {
        Stop-Process -Id $h.ProcessId -Force -ErrorAction SilentlyContinue
        Log "closed WSL file helper dllhost.exe ($($h.ProcessId))"
    }
}

function Stop-DriveHolders([string]$InstanceId, [datetime]$Since) {
    # Windows logs which program blocked restarting the drive (System event 225).
    # Close the Explorer helper processes (dllhost.exe) named there; Windows starts
    # them again when needed. Nothing else is closed.
    $events = @(Get-WinEvent -FilterHashtable @{LogName = 'System'; Id = 225; StartTime = $Since} -ErrorAction SilentlyContinue |
                Where-Object { $_.Message -like "*$InstanceId*" })
    $ids = @($events | ForEach-Object { if ($_.Message -match 'process id (\d+)') { [int]$Matches[1] } } | Select-Object -Unique)
    foreach ($id in $ids) {
        $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -eq 'dllhost') {
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
            Log "closed Explorer helper dllhost.exe ($id) holding the drive"
        } elseif ($proc) {
            Log "drive held by $($proc.ProcessName) ($id)"
        }
    }
}

function Attach-Disk([int]$DiskNumber) {
    # Returns the session record kept in $StateFile: usbip|<busid>|<keep-alive pid>
    Ensure-Usbipd
    # Start from a clean Linux side: a mount left by an earlier session (its drive gone)
    # would block mounting the drive again.
    Invoke-Wsl @('--terminate', $Distro) -AllowFail | Out-Null
    $busId = Get-DiskBusId $DiskNumber
    if (-not $busId) { Fail 'Could not find the PortableTAK drive among the USB devices. Unplug it, plug it in again and retry.' }
    Invoke-Usbipd @('bind', '--busid', $busId) -AllowFail | Out-Null
    # Keep PortableTAK's Linux side running while the server runs (WSL stops an idle one).
    $keep = Start-Process wsl.exe -ArgumentList @('-d', $Distro, '-u', 'root', '--', 'sleep', 'infinity') -WindowStyle Hidden -PassThru
    Invoke-Distro 'modprobe -a vhci_hcd usb-storage uas' -AllowFail | Out-Null
    # Windows must let go of the drive: dismount its volumes and remove their drive
    # letters, so Explorer, indexing or antivirus cannot open them again (the letter
    # comes back when the drive is plugged in again or given back on stop).
    Log "releasing the drive's volumes (disk $DiskNumber)"
    Stop-WslFileHelper
    foreach ($part in @(Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue)) {
        foreach ($letter in @($part.DriveLetter)) {
            if ("$letter" -match '^[A-Za-z]$') {
                $path = "${letter}:\"
                $vol = Get-CimInstance Win32_Volume -Filter "DriveLetter='$($path.Substring(0, 2))'" -ErrorAction SilentlyContinue
                if ($vol) { Invoke-CimMethod -InputObject $vol -MethodName Dismount -Arguments @{Force = $true; Permanent = $false} -ErrorAction SilentlyContinue | Out-Null }
                $ErrorActionPreference = 'Continue'
                $mv = & mountvol.exe $path /p 2>&1 | Out-String
                $ErrorActionPreference = 'Stop'
                Log ("mountvol $path /p -> $LASTEXITCODE " + $mv.Trim())
            }
        }
    }
    $forced = $false
    $attach = Invoke-Usbipd @('attach', '--wsl', '--busid', $busId) -AllowFail
    if ($attach.Code -ne 0 -and $attach.Out -match 'busy|used by Windows') {
        # Still held by Windows: hand it over anyway (given back with unbind on stop).
        Invoke-Usbipd @('bind', '--force', '--busid', $busId) | Out-Null
        $forced = $true
        # Windows can take a few seconds to let go after a forced bind.
        for ($try = 0; $try -lt 6; $try++) {
            if ($try -eq 2) {
                # Still held: restart the USB device so the handover driver takes it.
                $inst = @((Invoke-Usbipd @('state')).Out | ConvertFrom-Json | Select-Object -ExpandProperty Devices |
                          Where-Object { $_.BusId -eq $busId } | Select-Object -ExpandProperty InstanceId)
                Log ("restarting USB device ${busId}: " + ($inst -join ','))
                if ($inst) {
                    for ($round = 0; $round -lt 3; $round++) {
                        $since = (Get-Date).AddSeconds(-5)
                        Stop-WslFileHelper
                        Dismount-DiskVolumes $DiskNumber
                        $ErrorActionPreference = 'Continue'
                        $rs = & pnputil.exe /restart-device "$($inst[0])" 2>&1 | Out-String
                        $code = $LASTEXITCODE
                        $ErrorActionPreference = 'Stop'
                        Log ("pnputil restart $($inst[0]) -> $code " + $rs.Trim())
                        if ($code -eq 0) { break }
                        Start-Sleep -Seconds 8   # Windows logs the blocking program a few seconds later
                        Stop-DriveHolders $inst[0] $since
                    }
                    Start-Sleep -Seconds 3
                }
            }
            Start-Sleep -Seconds 3
            $attach = Invoke-Usbipd @('attach', '--wsl', '--busid', $busId) -AllowFail
            if ($attach.Code -eq 0 -or $attach.Out -notmatch 'busy|used by Windows') { break }
        }
    }
    if ($attach.Code -ne 0) {
        if ($forced) { Invoke-Usbipd @('unbind', '--busid', $busId) -AllowFail | Out-Null }
        Stop-Process -Id $keep.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        Restore-DriveLetter
        Fail ('Windows would not let go of the drive. Close any File Explorer windows, unplug the drive, plug it in again and retry. (' + ((($attach.Out -replace "`r", '') -split "`n" | Where-Object { $_ -match 'error' }) -join ' ').Trim() + ')')
    }
    $seen = Invoke-Distro 'modprobe -a usb-storage uas 2>/dev/null; for i in $(seq 1 30); do lsblk -dno TRAN | grep -q usb && exit 0; sleep 1; done; exit 1' -AllowFail
    if ($seen.Code -ne 0) {
        Invoke-Usbipd @('detach', '--busid', $busId) -AllowFail | Out-Null
        if ($forced) { Invoke-Usbipd @('unbind', '--busid', $busId) -AllowFail | Out-Null }
        Stop-Process -Id $keep.Id -Force -ErrorAction SilentlyContinue
        Fail 'The drive did not appear inside PortableTAK. Unplug it, plug it in again and retry.'
    }
    return "usbip|$busId|$($keep.Id)|$([int]$forced)"
}

function Detach-Disk([string]$Record) {
    $parts = $Record -split '\|'
    if ($parts[0] -eq 'usbip') {
        Invoke-Usbipd @('detach', '--busid', $parts[1]) -AllowFail | Out-Null
        # A forced handover keeps Windows away from the drive until it is unbound.
        if ($parts.Count -gt 3 -and $parts[3] -eq '1') { Invoke-Usbipd @('unbind', '--busid', $parts[1]) -AllowFail | Out-Null }
        if ($parts.Count -gt 2 -and $parts[2]) { Stop-Process -Id ([int]$parts[2]) -Force -ErrorAction SilentlyContinue }
        # Windows sees the drive again after a few seconds; then restore its letter.
        Start-Sleep -Seconds 5
        Update-HostStorageCache -ErrorAction SilentlyContinue
        Restore-DriveLetter
    } else {
        Invoke-Wsl @('--unmount', $Record) -AllowFail | Out-Null    # sessions from older versions
    }
}

# --- start / stop ------------------------------------------------------------------
function Start-PortableTAK {
    $drive = Find-Drive
    if (-not $drive) {
        if (Test-Path $StateFile) { Fail 'PortableTAK is already running from a drive. Use "Stop PortableTAK" first.' }
        Fail 'No PortableTAK drive found. Plug in the drive and try again.'
    }
    if (Test-Path $StateFile) {
        # Windows can see the drive again, so the previous session's drive was unplugged
        # (or Windows took it back) without "Stop PortableTAK": clean that session up.
        Say 'Cleaning up the previous session (its drive was unplugged without stopping)...'
        Stop-PortableTAK -Quiet
    }
    Say ("PortableTAK drive found: {0} ({1:N0} GB, disk {2})" -f $drive.Name, ($drive.Size / 1GB), $drive.Disk) 'Cyan'
    $build = Test-Windows
    Ensure-Wsl
    Ensure-Networking $build
    Ensure-Distro $drive.Root
    Ensure-Shortcuts
    $addresses = @(Get-ServiceAddress)
    $address = $addresses[0]
    $others = ($addresses | Select-Object -Skip 1 -First 3) -join ','
    Say "This computer's address for phones and WinTAK: $address" 'Cyan'
    # Remote access uses the drive's own Tailscale (set up once in the dashboard); this
    # computer's own Tailscale app is not needed and its address is not used.
    Say 'Attaching the drive to PortableTAK (it disappears from File Explorer while the server runs)...'
    $record = Attach-Disk $drive.Disk
    Set-Content -Path $StateFile -Value $record
    Say 'Starting the server (first start on a computer takes a few minutes)...'
    $result = Invoke-Distro "/usr/local/portabletak/bin/portabletak-wsl-start $address $others" -AllowFail
    if ($result.Out -match 'PORTABLETAK_AUTOLOAD=RUNNING') {
        Invoke-Distro 'rm -f /usr/local/portabletak/release/first-run-enabled' -AllowFail | Out-Null
        Say 'PortableTAK is running.' 'Green'
        Say "  Phones and WinTAK connect to $address (TAK port 8089, SSL)." 'Green'
        Start-Process 'http://localhost:8090/'
    } elseif ($result.Out -match 'PORTABLETAK_PROVISIONING_STATE=new') {
        Say 'This drive is blank. Let''s set it up (one time).' 'Yellow'
        Invoke-FirstSetup
        Invoke-Distro 'rm -f /usr/local/portabletak/release/first-run-enabled' -AllowFail | Out-Null
        Say 'PortableTAK is set up and running.' 'Green'
        Say "  Phones and WinTAK connect to $address (TAK port 8089, SSL)." 'Green'
        Start-Process 'http://localhost:8090/'
    } elseif ($result.Out -match 'PORTABLETAK_AUTOLOAD=MULTIPLE_CARTRIDGES') {
        Stop-PortableTAK -Quiet
        Fail 'More than one PortableTAK drive is attached. Unplug the others and start again.'
    } else {
        Say 'PortableTAK did not start. Stopping safely so the drive can be removed.' 'Red'
        Stop-PortableTAK -Quiet
        Fail 'Start failed.'
    }
}

function Read-Plain([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Read-NewPassword([string]$Label) {
    while ($true) {
        $first = Read-Plain "$Label (8+ characters; 12+ recommended)"
        if ($first.Length -lt 8 -or $first.Length -gt 128 -or $first -match '[^\x20-\x7E]') {
            Say '  Use 8 to 128 ordinary characters. Try again.' 'Yellow'; continue
        }
        if ((Read-Plain 'Type it again') -ne $first) { Say '  They did not match. Try again.' 'Yellow'; continue }
        if ($first.Length -lt 12) { Say '  Note: under 12 characters is easier to guess. Use 12+ for real use.' 'DarkYellow' }
        return $first
    }
}

function Invoke-FirstSetup {
    while ($true) {
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
        Invoke-Distro 'mkdir -p /usr/local/portabletak/release && touch /usr/local/portabletak/release/first-run-enabled' | Out-Null
        $json = @{ instance_name = $name; administrator_password = $admin; client_password = $client } | ConvertTo-Json -Compress
        $admin = $null; $client = $null
        Say 'Setting up the server (about 5-10 minutes; progress shows at the top of this window)...'
        # Answers go in through a private file (never on a command line); it is opened
        # and deleted before setup starts, and fed to setup on standard input.
        Invoke-Distro 'rm -rf /var/tmp/pt-first && mkdir -m 700 /var/tmp/pt-first' | Out-Null
        [IO.File]::WriteAllText("\\wsl.localhost\$Distro\var\tmp\pt-first\answers.json", $json, (New-Object Text.UTF8Encoding($false)))
        $json = $null
        $out = (Invoke-DistroLive 'f=/var/tmp/pt-first/answers.json; chmod 600 "$f"; exec 3<"$f"; rm -rf /var/tmp/pt-first; /usr/local/portabletak/bin/portabletak-wsl-provision <&3' 'Setting up the server').Out
        Log ("first setup -> " + (($out -split "`n" | Where-Object { $_ -match 'PORTABLETAK_SETUP=|_ERROR=' }) -join ' '))
        if ($out -match 'PORTABLETAK_SETUP=COMPLETE') { return }
        if ($out -match 'PORTABLETAK_SETUP=INVALID') {
            Say ('  Setup did not accept that: ' + (($out -split "`n" | Where-Object { $_ -match '_ERROR=' }) -join ' ')) 'Yellow'
            continue
        }
        Fail ('First setup failed. ' + (($out -split "`n" | Where-Object { $_ -match '_ERROR=' }) -join ' '))
    }
}

function Stop-PortableTAK([switch]$Quiet) {
    if (-not (Test-Path $StateFile)) { if (-not $Quiet) { Say 'PortableTAK is not running from a drive.' }; return }
    $path = Get-Content $StateFile | Select-Object -First 1
    Say 'Stopping PortableTAK safely...'
    $stop = Invoke-Distro '/usr/local/portabletak/bin/portabletak-wsl-stop' -AllowFail
    if ($stop.Out -notmatch 'PORTABLETAK_SAFE_TO_REMOVE=1|PORTABLETAK_STORAGE=UNMOUNTED|cartridge_not_mounted') {
        # Same clean-up a node does when a drive is pulled: stop everything, unmount.
        Say 'The server did not confirm a clean stop; stopping it the hard way.' 'Yellow'
        Invoke-Distro '/usr/local/portabletak/bin/portabletak-emergency-stop; sync; for m in $(awk ''$2 ~ "^/mnt/asgard" {print $2}'' /proc/mounts | sort -r); do umount "$m" || umount -l "$m"; done; sync' -AllowFail | Out-Null
    }
    Detach-Disk $path
    Invoke-Wsl @('--terminate', $Distro) -AllowFail | Out-Null
    Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
    Say 'PortableTAK stopped. You can remove the drive now.' 'Green'
}

function Show-Status {
    if (Test-Path $StateFile) { Invoke-Distro '/usr/local/portabletak/bin/portabletak-health' -AllowFail | ForEach-Object { $_.Out } }
    else { Say 'PortableTAK is not running from a drive.' }
}

# --- automatic start when the drive is plugged in ------------------------------------
function Install-Helper {
    $script = Join-Path $Home2 'PortableTAK.ps1'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" watch"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0
    Register-ScheduledTask -TaskName 'PortableTAK Helper' -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName 'PortableTAK Helper'
    Say 'PortableTAK will now start automatically when its drive is plugged in.' 'Green'
}

function Remove-Helper {
    Unregister-ScheduledTask -TaskName 'PortableTAK Helper' -Confirm:$false -ErrorAction SilentlyContinue
    Say 'Automatic start turned off.' 'Green'
}

function Watch-Drives {
    Register-CimIndicationEvent -ClassName Win32_VolumeChangeEvent -SourceIdentifier PortableTAKVolume | Out-Null
    while ($true) {
        $event = Wait-Event -SourceIdentifier PortableTAKVolume
        Remove-Event -EventIdentifier $event.EventIdentifier
        if ($event.SourceEventArgs.NewEvent.EventType -ne 2) { continue }   # 2 = arrival
        Start-Sleep -Seconds 3
        if ((Find-Drive) -and -not (Test-Path $StateFile)) {
            Start-Process powershell.exe -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', (Join-Path $Home2 'PortableTAK.ps1'), 'start')
        }
    }
}

function Ensure-Shortcuts {
    # While the server runs the drive is attached to WSL and hidden from Windows,
    # so Stop and Dashboard live on the desktop.
    $shell = New-Object -ComObject WScript.Shell
    $desktop = [Environment]::GetFolderPath('Desktop')
    $script = Join-Path $Home2 'PortableTAK.ps1'
    foreach ($item in @(@('Stop PortableTAK', "-NoProfile -ExecutionPolicy Bypass -File `"$script`" stop"),
                        @('PortableTAK Status', "-NoProfile -ExecutionPolicy Bypass -File `"$script`" status"))) {
        $link = $shell.CreateShortcut((Join-Path $desktop ($item[0] + '.lnk')))
        $link.TargetPath = 'powershell.exe'; $link.Arguments = $item[1]; $link.WorkingDirectory = $Home2
        $link.Save()
    }
    $url = $shell.CreateShortcut((Join-Path $desktop 'PortableTAK Dashboard.url'))
    $url.TargetPath = 'http://localhost:8090/'; $url.Save()
}

# --- entry ---------------------------------------------------------------------------
# PortableTAK Setup for Windows loads these functions without running anything.
if ($env:PORTABLETAK_LIBRARY -eq '1') { return }
$runningLocal = $PSCommandPath -and $PSCommandPath.StartsWith($Home2, [StringComparison]::OrdinalIgnoreCase)
if ($Action -eq 'start' -and -not $runningLocal) {
    # Started from the drive: copy the tools to this computer and run from there.
    $driveRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    Copy-Tools $driveRoot
    $DriveLetter = $driveRoot.Substring(0, 1)
}
if ($Action -ne 'watch' -and (-not (Test-Admin) -or -not $runningLocal)) {
    $local = Join-Path $Home2 'PortableTAK.ps1'
    if (-not (Test-Path $local)) { Fail 'PortableTAK is not set up on this computer yet. Start it from the drive first.' }
    $args2 = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $local, $Action)
    if ($DriveLetter) { $args2 += @('-DriveLetter', $DriveLetter) }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $args2
    exit 0
}
Log "action $Action"
switch ($Action) {
    'start'          { Start-PortableTAK }
    'stop'           { Stop-PortableTAK }
    'status'         { Show-Status }
    'install-helper' { Install-Helper }
    'remove-helper'  { Remove-Helper }
    'watch'          { Watch-Drives }
    default          { Fail "Unknown action '$Action'." }
}
if ($Host.Name -eq 'ConsoleHost' -and $Action -in @('start', 'stop', 'status', 'install-helper', 'remove-helper')) {
    Read-Host 'Press Enter to close' | Out-Null
}
