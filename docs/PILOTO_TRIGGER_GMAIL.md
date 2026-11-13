# Piloto controlado del receptor Gmail aislado

**Estado: preparación solamente.** No ejecutar hasta completar autorización humana del piloto y del consentimiento OAuth. Este procedimiento no habilita `gmail_reception_enabled` en el supervisor completo y no modifica ANKA 01.

## Aislamiento

El único punto de entrada es `python -m anka.cli.gmail_reception`. Reutiliza `anka.servicios.recepcion.sondear`, escribe la cola/auditoría en la SQLite configurada en `%LOCALAPPDATA%\AnkaCorreos`, y no importa ni arranca el supervisor, el puente Work, Drive, Sheets, ni rutinas de envío. La frecuencia predeterminada es 60 segundos; se acepta de 30 a 3600 segundos. `--once` ejecuta exactamente un sondeo y consulta Gmail. **Una sola instancia por equipo (D-151):** el receptor toma un candado del sistema sobre `gmail_reception.lock` en la carpeta de datos; un segundo receptor se niega a arrancar y dice el PID y la hora del que lo tiene. Si la instancia anterior murió de golpe, el sistema libera el candado solo y el nuevo arranque avisa en el registro que recuperó un bloqueo obsoleto. Cada ciclo informa también `history_seen` (mensajes que reportó Gmail) y `already_registered` (los que ya estaban); `discovered: 0` no significa que no haya llegado nada. `cursor_actualizado` solo se audita cuando el cursor cambia.

La cuenta se autoriza de forma independiente con el comando `--authorize`. El token queda en `gmail_reception_token.json`, separado de `token.json`. El receptor valida que el archivo declare solo `https://www.googleapis.com/auth/gmail.readonly`, comprueba el alcance concedido cuando Google lo informa y falla cerrado si falta o sobra un alcance. Nunca usa `anka.infra.google_services.services()` ni cae al token comercial. La autorización requiere navegador e intervención de una persona. No se ha realizado en esta preparación.

Las llamadas del servicio están limitadas por ese alcance de solo lectura. El cliente Gmail de Google no incluye cliente Drive. Las migraciones pendientes de recepción se aplicarán a la SQLite local cuando el receptor acceda a ella por primera vez; por eso exige respaldo verificado y autorización previa. La migración 23 reconstruye la tabla de auditoría si falta en una base marcada v22. La primera pasada fija el cursor sin importar el historial.

## Verificación previa

1. Confirmar que no existe ya un receptor aislado en ejecución y que la ruta efectiva de SQLite corresponde a la cuenta prevista.
2. Tener un respaldo previo verificado y un resultado limpio de `PRAGMA integrity_check`.
3. Ejecutar `python -m anka.cli.gmail_reception --authorize`, completar OAuth y verificar que se creó `gmail_reception_token.json`. No mostrar ni copiar su contenido. Si Google concede ámbitos adicionales, el comando falla y no guarda la credencial.
4. Acordar por separado la fecha, duración, observador y autorización para que la primera pasada real establezca el cursor y aplique las migraciones pendientes.

La autorización (paso interactivo, no sondeo) se inicia desde la raíz del proyecto:

```powershell
Set-Location D:\Automatizacion_Correos
.\.venv\Scripts\python.exe -m anka.cli.gmail_reception --authorize
```

Verificar el alcance sin imprimir el token:

```powershell
@'
import json, os, pathlib
p = pathlib.Path(os.environ["LOCALAPPDATA"]) / "AnkaCorreos" / "gmail_reception_token.json"
scopes = json.loads(p.read_text(encoding="utf-8")).get("scopes", [])
assert scopes == ["https://www.googleapis.com/auth/gmail.readonly"], "Alcance inesperado"
print("Credencial dedicada: Gmail readonly verificado")
'@ | .\.venv\Scripts\python.exe -
```

## Inicio y observación

Desde PowerShell, abrir una ventana dedicada:

```powershell
Set-Location D:\Automatizacion_Correos
.\.venv\Scripts\python.exe -m anka.cli.gmail_reception --interval 60
```

La ejecución permanece en primer plano. En su primera pasada fija el cursor y no importa mensajes anteriores; las siguientes consultan Gmail cada 60 segundos. Revisar en otra ventana únicamente el estado SQLite en modo lectura (`recepcion_control`, `recepcion_mensajes`, `recepcion_eventos`) y el log local. Correlacionar IDs y resultados sin cambiar estados de Gmail. No enviar correos de prueba a la cuenta compartida; ANKA 01 permanece intacta y podría reaccionar a ese correo.

Consulta de observación en modo estrictamente de lectura:

```powershell
@'
import os, pathlib, sqlite3
p = pathlib.Path(os.environ["LOCALAPPDATA"]) / "AnkaCorreos" / "quotes.sqlite3"
db = sqlite3.connect("file:" + p.as_posix() + "?mode=ro", uri=True)
db.execute("PRAGMA query_only=ON")
print("schema", db.execute("PRAGMA user_version").fetchone()[0])
print("integrity", db.execute("PRAGMA integrity_check").fetchone()[0])
print("control", [tuple(r) for r in db.execute("SELECT history_id,recovery_state,last_success_at,consecutive_errors FROM recepcion_control")])
print("queue", [tuple(r) for r in db.execute("SELECT state,COUNT(*) FROM recepcion_mensajes GROUP BY state ORDER BY state")])
print("events", [tuple(r) for r in db.execute("SELECT event_kind,occurred_at FROM recepcion_eventos ORDER BY id DESC LIMIT 30")])
db.close()
'@ | .\.venv\Scripts\python.exe -
```

Señales esperadas: `inicializado_sin_historial` en la primera pasada; luego `ok`, deduplicación estable, cursor avanzando solo después de la consulta completada y sin errores repetidos ni `revision_manual`.

## Detención y reversión

Presionar **Ctrl+C** en la ventana del receptor y confirmar `Receptor detenido.`. Verificar que el proceso haya terminado y que no quede otro `python -m anka.cli.gmail_reception`. Detener conserva el cursor y la cola. No borra ni revierte migraciones. Si la observación se interrumpe o la cobertura es dudosa, preservar la base y revisar eventos antes de reanudar; no reinicializar el cursor.

Para regresar el código, basta con no volver a ejecutar el módulo; no hay una bandera que activar en el supervisor. No restaurar una copia de SQLite sobre una base que haya recibido escrituras posteriores sin reconciliarlas. Las migraciones de recepción son aditivas y no se revierten bajando `user_version`.

## Límites

La ventana del piloto no garantiza recuperación de mensajes eliminados permanentemente ni de huecos que superen los límites documentados de recuperación. El consentimiento OAuth solo lectura restringe las operaciones de API disponibles, pero no impide que una persona con acceso a Windows modifique el proceso o la credencial. Esta preparación no consulta Gmail real, no habilita el receptor, no inicia el supervisor y no conecta eventos con ChatGPT Work.
