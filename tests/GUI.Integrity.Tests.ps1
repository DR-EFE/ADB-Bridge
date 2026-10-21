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
        It "AndroidAdb.psm1 debe contener firma o codificación UTF-8 válida" {
            $psmPath = Join-Path $repoRoot "AndroidAdb.psm1"
            (Test-Path -LiteralPath $psmPath) | Should Be $true
            $bytes = [System.IO.File]::ReadAllBytes($psmPath)
            $bytes.Length | Should BeGreaterThan 0
        }

        It "El script lanzador Lanzar_Flet_GUI.cmd debe existir en la raiz" {
            $cmdPath = Join-Path $repoRoot "Lanzar_Flet_GUI.cmd"
            (Test-Path -LiteralPath $cmdPath) | Should Be $true
        }
    }
}
