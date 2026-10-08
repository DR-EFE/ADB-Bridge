$filePath = "c:\Users\Desarrollo\Documents\Android\AndroidTools.GUI.ps1"
$content = [System.IO.File]::ReadAllText($filePath, [System.Text.Encoding]::UTF8)
$errors = $null
$tokens = $null
[System.Management.Automation.Language.Parser]::ParseInput($content, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) {
    Write-Host "Errors found: $($errors.Count)"
    foreach ($err in $errors) {
        Write-Host "Line $($err.Extent.StartLineNumber): $($err.Message)"
    }
} else {
    Write-Host "Sintaxis 100% Correcta sin Errores"
}
