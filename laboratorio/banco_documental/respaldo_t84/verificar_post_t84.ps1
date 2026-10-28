# Verificación posterior al despliegue del indicador (T-84). SOLO LECTURA: no inicia, detiene ni escribe nada de ANKA.
# Uso:  powershell -NoProfile -File laboratorio\banco_documental\respaldo_t84\verificar_post_t84.ps1 [-Desde "HH:mm:ss"]
param([string]$Desde = "")
$ErrorActionPreference = "Continue"
$raiz = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$datos = Join-Path $env:LOCALAPPDATA "AnkaCorreos"
$py = Join-Path $raiz ".venv\Scripts\python.exe"
$fallos = 0
function Resultado($ok, $nombre, $detalle) {
    if ($ok) { "[OK]    $nombre - $detalle" } else { "[FALLA] $nombre - $detalle"; $script:fallos++ }
}
"Verificación T-84 a las $(Get-Date -Format 'HH:mm:ss')"

# 1. Una sola instancia: un lanzador y un servicio (los python.exe de uv-python son los envoltorios de venv)
$sup = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'supervisor\.py' -and $_.CommandLine -notmatch 'uv-python' })
$servicios = @($sup | Where-Object { $_.CommandLine -match '--servicio' })
$lanzadores = @($sup | Where-Object { $_.CommandLine -notmatch '--servicio' })
Resultado ($servicios.Count -eq 1 -and $lanzadores.Count -eq 1) "Una sola instancia" "lanzadores=$($lanzadores.Count) servicios=$($servicios.Count); servicio iniciado $(if ($servicios) { '{0:HH:mm:ss}' -f $servicios[0].CreationDate })"

# 2 y 4. Dashboard y texto del indicador (backend: /state)
try {
    $r = Invoke-WebRequest "http://127.0.0.1:8765/state" -UseBasicParsing -TimeoutSec 5
    $estado = $r.Content | ConvertFrom-Json
    Resultado ($r.StatusCode -eq 200) "Dashboard HTTP" "200 en /state"
    Resultado ($estado.bridge -eq "ok") "Indicador «Revisión de correos»" "bridge=$($estado.bridge) :: $($estado.text)"
    Resultado ($estado.text -notmatch 'Puente Work') "Sin instrucción obsoleta" "el texto no menciona el puente apagado"
} catch { Resultado $false "Dashboard HTTP" "sin respuesta en 127.0.0.1:8765" }

# 3 y 6. Receptor (recepcion_control), coordinación y duplicados: lectura con mode=ro
$codigo = @'
import json, os, sqlite3, sys
from datetime import datetime
base = os.path.join(os.environ["LOCALAPPDATA"], "AnkaCorreos", "quotes.sqlite3")
db = sqlite3.connect("file:" + base.replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
c = db.execute("SELECT last_success_at,last_attempt_at,consecutive_errors,recovery_state FROM recepcion_control "
               "ORDER BY updated_at DESC LIMIT 1").fetchone()
edad = (datetime.now().astimezone() - datetime.fromisoformat(c[0])).total_seconds() if c and c[0] else None
dup = db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM trabajos_work GROUP BY account,thread_id,message_id,job_type,"
                 "contract_version HAVING COUNT(*)>1)").fetchone()[0]
estados = dict(db.execute("SELECT state,COUNT(*) FROM trabajos_work GROUP BY state").fetchall())
print(json.dumps({"exito": c[0] if c else None, "edad": edad, "errores": c[2] if c else None, "recuperacion": c[3] if c else None,
                  "integridad": db.execute("PRAGMA integrity_check").fetchone()[0], "duplicados": dup, "trabajos": estados}))
'@
$j = $codigo | & $py -I - 2>&1 | Select-Object -Last 1
try {
    $d = $j | ConvertFrom-Json
    Resultado ($null -ne $d.edad -and $d.edad -lt 180 -and $d.errores -eq 0) "Receptor (recepcion_control)" "último éxito $($d.exito) (hace $([int]$d.edad) s), errores=$($d.errores), recuperación=$($d.recuperacion)"
    Resultado ($d.integridad -eq "ok" -and $d.duplicados -eq 0) "Base íntegra y sin duplicados" "integrity_check=$($d.integridad), duplicados=$($d.duplicados), trabajos=$($d.trabajos | ConvertTo-Json -Compress)"
} catch { Resultado $false "Lectura de SQLite" "$j" }

# 5. Coordinación activa: el candado se toca en cada ciclo (cada 300 s por defecto); si ya pasó un ciclo, su hora es posterior al reinicio
$lock = Get-Item (Join-Path $datos "work_coordinacion.lock") -ErrorAction SilentlyContinue
if ($lock -and $servicios) {
    $despues = $lock.LastWriteTime -ge $servicios[0].CreationDate
    Resultado $despues "Coordinación activa" "candado tocado a las $('{0:HH:mm:ss}' -f $lock.LastWriteTime) (servicio iniciado $('{0:HH:mm:ss}' -f $servicios[0].CreationDate))"
} else { Resultado $false "Coordinación activa" "no se encontró el candado o el servicio" }

# 7. Errores nuevos en el registro desde el reinicio
$ini = if ($servicios) { $servicios[0].CreationDate } else { (Get-Date).AddMinutes(-10) }
$malas = @(Get-Content (Join-Path $datos "logs\anka.log") -Tail 300 -Encoding UTF8 | Where-Object {
    $_ -match '^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (WARNING|ERROR|CRITICAL)' -and [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null) -ge $ini.AddSeconds(-5) })
Resultado ($malas.Count -eq 0) "Sin avisos ni errores nuevos" "$($malas.Count) línea(s) WARNING/ERROR desde el reinicio"
$malas | Select-Object -First 5 | ForEach-Object { "         $_" }
"Resumen: $(if ($fallos -eq 0) { 'TODO EN ORDEN' } else { "$fallos verificación(es) con falla: ver la reversión" })"
exit $fallos
