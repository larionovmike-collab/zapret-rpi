[CmdletBinding()]
param(
    [string]$HostName,
    [string]$User,
    [string]$Ssid = 'Zapret-RPi',
    [ValidateSet(1, 6, 11)][int]$Channel = 6,
    [ValidatePattern('^[A-Z]{2}$')][string]$Country = 'RU',
    [string]$IdentityFile
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repo '.env'
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            $name, $value = $matches[1], $matches[2].Trim('"').Trim("'")
            if (-not (Get-Variable -Name $name -Scope Script -ErrorAction SilentlyContinue)) {
                Set-Variable -Name $name -Value $value -Scope Script
            }
        }
    }
}
if (-not $HostName) { $HostName = $PI_HOST }
if (-not $User) { $User = $PI_USER }
if (-not $HostName -or -not $User) { throw 'Specify -HostName and -User, or PI_HOST/PI_USER in .env.' }

$secure = Read-Host 'WPA2 passphrase (8-63: letters, digits, . ! _ -)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $passphrase = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
if ($passphrase -notmatch '^[A-Za-z0-9.!_-]{8,63}$') { throw 'Invalid WPA2 passphrase format.' }
if ($Ssid -notmatch '^[A-Za-z0-9._-]{1,32}$') { throw 'Invalid SSID format.' }
$temp = Join-Path ([IO.Path]::GetTempPath()) ("zapret-rpi-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    $archive = Join-Path $temp 'deploy.tar.gz'
    $config = Join-Path $temp 'deploy.conf'
    $configText = (@($Ssid, $passphrase, $Country, $Channel) -join "`n") + "`n"
    [IO.File]::WriteAllText($config, $configText, [Text.UTF8Encoding]::new($false))
    & tar --exclude='web/frontend/node_modules' -czf $archive -C $repo VERSION UPSTREAM_COMMIT configs systemd scripts web
    if ($LASTEXITCODE) { throw 'Failed to create deployment archive.' }

    $sshArgs = @('-o', 'StrictHostKeyChecking=yes')
    if ($IdentityFile) { $sshArgs += @('-i', $IdentityFile) }
    $target = "${User}@${HostName}"
    & ssh @sshArgs $target 'uname -a; cat /etc/os-release; ip -br address; ip route; systemctl is-active ssh'
    if ($LASTEXITCODE) { throw 'Remote preflight failed.' }
    & scp @sshArgs $archive $config "${target}:/tmp/"
    if ($LASTEXITCODE) { throw 'Upload failed.' }
    & ssh @sshArgs $target 'rm -rf /tmp/zapret-rpi-deploy; mkdir -m 700 /tmp/zapret-rpi-deploy; tar -xzf /tmp/deploy.tar.gz -C /tmp/zapret-rpi-deploy; chmod +x /tmp/zapret-rpi-deploy/scripts/*.sh; sudo /tmp/zapret-rpi-deploy/scripts/install.sh --config /tmp/deploy.conf; rc=$?; rm -f /tmp/deploy.conf /tmp/deploy.tar.gz; exit $rc'
    if ($LASTEXITCODE) { throw 'Remote deployment failed.' }
}
finally {
    $passphrase = $null
    Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
}
