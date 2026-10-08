$filePath = "c:\Users\Desarrollo\Documents\Android\AndroidTools.GUI.ps1"
$bytes = [System.IO.File]::ReadAllBytes($filePath)
$str = [System.Text.Encoding]::UTF8.GetString($bytes)
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($filePath, $str, $utf8Bom)
Write-Host "Re-encoded AndroidTools.GUI.ps1 with UTF-8 BOM"
