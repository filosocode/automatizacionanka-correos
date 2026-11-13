# Verificación posterior al despliegue de la cancelación (T-83). SOLO LECTURA: no inicia, detiene ni escribe nada de ANKA.
# Uso:  powershell -NoProfile -File laboratorio\banco_documental\respaldo_t83\verificar_post_t83.ps1
$ErrorActionPreference = "Continue"
$raiz = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$py = Join-Path $raiz ".venv\Scripts\python.exe"
$fallos = 0
function Resultado($ok, $nombre, $detalle) {
    if ($ok) { "[OK]    $nombre - $detalle" } else { "[FALLA] $nombre - $detalle"; $script:fallos++ }
}
"Verificación T-83 a las $(Get-Date -Format 'HH:mm:ss')"

# 1. Lo que ya garantizaba T-84: instancia única, dashboard, indicador «activa», receptor sano, coordinación, base íntegra, sin duplicados, sin errores
$t84 = Join-Path $raiz "laboratorio\banco_documental\respaldo_t84\verificar_post_t84.ps1"
$salidaT84 = & powershell -NoProfile -File $t84 2>&1
$salidaT84 | ForEach-Object { "  | $_" }
Resultado ($LASTEXITCODE -eq 0) "Controles de T-84 (siguen vigentes)" "código de salida $LASTEXITCODE"

# 2. El código nuevo está en el servicio que corre: los módulos existen y el indicador de T-84 sigue en su sitio
Resultado ((Test-Path (Join-Path $raiz "anka\servicios\cancelaciones.py")) -and (Test-Path (Join-Path $raiz "anka\infra\lapidas.py")) -and
           (Test-Path (Join-Path $raiz "anka\dominio\cancelacion_work.py"))) "Módulos de cancelación presentes" "cancelaciones, lapidas, cancelacion_work"
Resultado ((Select-String -Path (Join-Path $raiz "anka\infra\work_heartbeat.py") -Pattern "_estado_de_la_recepcion" -Quiet)) "Corrección T-84 conservada" "work_heartbeat.py"

# 3. La orden de solo lectura de T-83: el diario de lápidas y la base coinciden (sin diario hay 0 entradas y todo en orden)
Push-Location $raiz
$env:PYTHONIOENCODING = "utf-8"
$j = & $py -m anka.cli.work_jobs verificar-lapidas 2>&1 | Select-Object -Last 1
Pop-Location
try {
    $v = $j | ConvertFrom-Json
    Resultado ($v.en_orden_total -eq $true) "Diario de lápidas y base" "entradas=$($v.entradas) lineas_rotas=$($v.lineas_rotas) faltan=$(@($v.faltan).Count) chocan=$(@($v.chocan).Count)"
} catch { Resultado $false "verificar-lapidas" "$j" }

# 4. Cancelaciones: solo las AUTORIZADAS (registro cancelaciones_autorizadas.json: hoy la de T-91B), consistentes entre SQLite, eventos y
#    diario de lápidas, y sin publicación posterior (hoja, eventos o archivos en Drive). Cualquier otra cancelación o incoherencia falla.
Push-Location $raiz
$a = (& $py -m laboratorio.banco_documental.respaldo_t83.auditoria_cancelaciones 2>&1 | Select-Object -Last 1)
Pop-Location
try {
    $r = $a | ConvertFrom-Json
    $detalle = "cancelaciones=$($r.cancelados) autorizadas y consistentes"
    if (-not $r.ok) { $detalle = "cancelaciones=$($r.cancelados); " + (@($r.problemas) -join ' | ') }
    Resultado ($r.ok -eq $true) "Cancelaciones autorizadas y sin publicación posterior" $detalle
} catch { Resultado $false "Auditoría de cancelaciones" "$a" }
"Resumen: $(if ($fallos -eq 0) { 'TODO EN ORDEN' } else { "$fallos verificación(es) con falla: ver la reversión" })"
exit $fallos
