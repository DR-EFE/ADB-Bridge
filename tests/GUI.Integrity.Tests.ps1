# =============================================================================
# PRUEBAS DE INTEGRIDAD Y SINTAXIS DE SCRIPTS (.ps1 / .psm1)
# =============================================================================

$repoRoot = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) ".."
$repoRoot = [System.IO.Path]::GetFullPath($repoRoot)

Describe "Integridad y Sintaxis de Scripts del Repositorio" {

    $scriptFiles = Get-ChildItem -Path $repoRoot -Recurse -Include *.ps1, *.psm1 | 
                    Where-Object { $_.FullName -notmatch '\\scratch\\' }

    Context "Analisis Sintactico de PowerShell" {
        foreach ($file in $scriptFiles) {
            It "El archivo $($file.Name) no debe tener errores de sintaxis" {
                $content = [System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)
                $errors = $null
                $tokens = $null
                [System.Management.Automation.Language.Parser]::ParseInput($content, [ref]$tokens, [ref]$errors)
                
                if ($errors.Count -gt 0) {
                    $errMsg = ($errors | ForEach-Object { "Linea $($_.Extent.StartLineNumber): $($_.Message)" }) -join "; "
                    $errMsg | Should BeNullOrEmpty
                } else {
                    $errors.Count | Should Be 0
                }
            }
        }
    }

    Context "Validacion de Codificacion de Archivos Críticos" {
        It "AndroidTools.GUI.ps1 debe contener firma BOM UTF-8 para compatibilidad con Windows PowerShell 5.1" {
            $guiPath = Join-Path $repoRoot "AndroidTools.GUI.ps1"
            $bytes = [System.IO.File]::ReadAllBytes($guiPath)
            
            $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
            $hasBom | Should Be $true
        }

        It "El script lanzador Lanzar_GUI.cmd debe existir en la raiz" {
            $cmdPath = Join-Path $repoRoot "Lanzar_GUI.cmd"
            (Test-Path -LiteralPath $cmdPath) | Should Be $true
        }
    }
}
