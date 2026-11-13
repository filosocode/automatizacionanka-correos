# Registra la tarea "ANKA Vigilante" en el Programador de tareas (T-97). NO SE EJECUTA SOLA: sin -Confirmar solo muestra que haria.
# Uso:  powershell -NoProfile -File laboratorio\estabilidad\instalar_tarea.ps1            (vista previa, no cambia nada)
#       powershell -NoProfile -File laboratorio\estabilidad\instalar_tarea.ps1 -Confirmar (registra la tarea)
# Es una tarea del USUARIO actual (sin privilegios elevados). Se quita con desinstalar_tarea.ps1.
param([switch]$Confirmar)
$ErrorActionPreference = "Stop"
$nombre = "ANKA Vigilante"
$raiz = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$usuario = "$env:USERDOMAIN\$env:USERNAME"
$pythonw = Join-Path $raiz ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "No existe $pythonw" }

$accion = New-ScheduledTaskAction -Execute $pythonw -Argument "-m laboratorio.estabilidad.vigilante ejecutar" -WorkingDirectory $raiz
# Dos disparadores: al iniciar sesion y cada 5 minutos (si el vigilante murio, vuelve a arrancar; si vive, "IgnoreNew" lo ignora).
$alIniciar = New-ScheduledTaskTrigger -AtLogOn -User $usuario
$cada5 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$ajustes = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited

"Tarea:       $nombre (usuario $usuario, sin privilegios elevados)"
"Accion:      $pythonw -m laboratorio.estabilidad.vigilante ejecutar   (carpeta $raiz)"
"Disparadores: al iniciar sesion + cada 5 minutos (una sola instancia; sin limite de tiempo)"
"Reinicio:    3 reintentos cada minuto si la accion falla; el vigilante mismo relanza ANKA y anota la causa de cada salida"
"Antes de registrar: ANKA NO debe estar corriendo desde 'Iniciar Anka.bat' (el vigilante no arranca otro lanzador si ve uno vivo)."
if (-not $Confirmar) { "VISTA PREVIA: no se registro nada. Agrega -Confirmar para registrar."; exit 0 }

if (Get-ScheduledTask -TaskName $nombre -ErrorAction SilentlyContinue) { throw "La tarea '$nombre' ya existe: quitela primero con desinstalar_tarea.ps1" }
Register-ScheduledTask -TaskName $nombre -Action $accion -Trigger @($alIniciar, $cada5) -Settings $ajustes -Principal $principal `
    -Description "Mantiene ANKA en marcha y anota por que se detiene (T-97). Parada deliberada: python -m laboratorio.estabilidad.vigilante detener" | Out-Null
"REGISTRADA. Para comprobar:  Get-ScheduledTask -TaskName '$nombre' | Get-ScheduledTaskInfo"
