$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
function Read-FunctionAst($path, $name) {
    $tokens = $null; $errors = $null
    $tree = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
    return $tree.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
}
function Assert-True($condition, $message) { if (-not $condition) { throw $message } }

# Execute the actual conversion worker, with USB disappearing as soon as
# tcpip restarts adbd. No commands are sent to a real phone.
$conversion = Read-FunctionAst (Join-Path $root 'AndroidTools.GUI.ps1') 'Start-UsbAutoWifiConversion'
$assignment = $conversion.Find({ param($node) $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$worker' }, $true)
$worker = [scriptblock]::Create($assignment.Right.Expression.ScriptBlock.Extent.Text)
$worker = & $worker
$script:usbAvailable = $true
$script:connected = $false
$script:probeReply = 'android-tools-ready'
function Invoke-AdbCommand {
    param($Serial, $Arguments)
    Assert-True $script:usbAvailable 'Conversion tried USB after restarting adbd.'
    switch ($Arguments -join ' ') {
        'shell getprop ro.product.model' { 'Test Phone' }
        'shell cat /sys/class/net/wlan0/address' { 'aa:bb:cc:dd:ee:ff' }
        'shell ip -4 addr show wlan0' { 'inet 192.168.1.25/24' }
        default { throw 'Unexpected USB command' }
    }
}
function Invoke-Adb {
    param($Serial, $Arguments)
    if ($Arguments[0] -eq 'tcpip') { $script:usbAvailable = $false; return }
    Assert-True ($Serial -eq '192.168.1.25:5555' -and $script:connected) 'Probe did not use connected WiFi endpoint.'
    if ($Arguments[0] -eq 'get-state') { return 'device' }
    return $script:probeReply
}
function Connect-AdbEndpoint { param($Endpoint, $TimeoutSeconds) $script:connected = $true }
function Start-Sleep { param($Seconds) }
$result = & $worker 'USB123'
Assert-True ($result.Success -and $result.WifiSerial -eq '192.168.1.25:5555') 'Conversion failed after USB disappeared.'
$script:usbAvailable = $true
$script:probeReply = 'unexpected reply'
$result = & $worker 'USB123'
Assert-True (-not $result.Success) 'Conversion accepted WiFi without a successful shell probe.'

# Restore a persisted WiFi selection even when USB appears first, and keep
# its identity (but not its connected state) when that endpoint disappears.
$selection = Read-FunctionAst (Join-Path $root 'AndroidAdb.psm1') 'Get-CurrentAdbDevice'
. ([scriptblock]::Create($selection.Extent.Text))
function Read-AdbDeviceStore {
    [pscustomobject]@{ CurrentDeviceId='phone'; Devices=@([pscustomobject]@{
        Id='phone'; FriendlyName='Phone'; LastEndpoint='192.168.1.25:5555'; UsbSerial='USB123'; Mode='tcpip-5555'; NetworkProfiles=@()
    }) }
}
$script:records = @(
    [pscustomobject]@{ Serial='USB123'; Status='device'; Raw='' }
    [pscustomobject]@{ Serial='192.168.1.25:5555'; Status='device'; Raw='' }
)
function Get-AdbDevices { $script:records }
$global:AndroidAdbCurrentDevice = $null
$global:AndroidAdbSelectionCleared = $false
$current = Get-CurrentAdbDevice
Assert-True ($current.Serial -eq '192.168.1.25:5555') 'USB replaced persisted WiFi selection.'
$script:records = @($script:records[0])
$current = Get-CurrentAdbDevice
Assert-True ($current.Serial -eq '192.168.1.25:5555' -and $current.Status -eq 'offline') 'Missing WiFi kept a stale connected state or switched to USB.'
$global:AndroidAdbCurrentDevice = $null
$current = Get-CurrentAdbDevice
Assert-True ($current.Serial -eq '192.168.1.25:5555' -and $current.Status -eq 'offline') 'A new worker forgot the unavailable WiFi selection.'
'PASS: conversion survives USB restart; WiFi requires a shell response; selection stays on WiFi; missing endpoint reports offline.'
