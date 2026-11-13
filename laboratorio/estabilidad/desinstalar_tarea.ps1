# Quita la tarea "ANKA Vigilante" (T-97). Sin -Confirmar solo muestra que haria. No detiene ANKA: para eso, el comando "detener".
# Uso:  powershell -NoProfile -File laboratorio\estabilidad\desinstalar_tarea.ps1 [-Confirmar]
param([switch]$Confirmar)
$ErrorActionPreference = "Stop"
$nombre = "ANKA Vigilante"
$t = Get-ScheduledTask -TaskName $nombre -ErrorAction SilentlyContinue
if (-not $t) { "La tarea '$nombre' no existe: nada que quitar."; exit 0 }
"Se quitaria la tarea '$nombre' (estado actual: $($t.State))."
if (-not $Confirmar) { "VISTA PREVIA: no se cambio nada. Agrega -Confirmar para quitarla."; exit 0 }
Stop-ScheduledTask -TaskName $nombre -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $nombre -Confirm:$false
"TAREA QUITADA. Si ANKA sigue corriendo, el vigilante ya no la vigila; para detenerla use: python -m laboratorio.estabilidad.vigilante detener"
