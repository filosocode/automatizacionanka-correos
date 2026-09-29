# Modo de operacion

El sistema puede trabajar en tres escenarios. Solo uno debe estar activo procesando Gmail.

## Regla principal

No correr dos orquestadores al mismo tiempo sobre la misma cuenta de Gmail.

Ejemplos de conflicto:

- bot local activo y tarea de ChatGPT Work activa;
- bot en modo API activo y bot en modo local activo;
- una ventana vieja con `--review-only` y otra con `--gmail-drafts`.

## Ver modo activo

```powershell
.\.venv\Scripts\python.exe app.py mode
```

## Activar modo local

Usa Ollama local. No consume API de OpenAI.

```powershell
.\.venv\Scripts\python.exe app.py set-mode --active-mode local
```

Para correr el piloto de borradores:

```powershell
.\.venv\Scripts\python.exe app.py run --apply --gmail-drafts --watch-seconds 300
```

El botón `Piloto borradores Gmail.bat` ejecuta exactamente este flujo y primero muestra el modo activo. La configuración entregada queda en modo `local`. Si se cambia a `chatgpt_work`, el mismo botón se detiene antes de tocar Gmail para evitar dos orquestadores.

En `config.json`, `own_addresses` debe incluir la cuenta principal y todos los aliases usados por Anka. El programa también consulta la dirección principal autenticada en Gmail y excluye esos mensajes antes de escoger remitente o analizar el hilo.

La política predeterminada para reenvíos es `forwarded_reply_policy: internal_reviewer`. El sistema intenta identificar al cliente original dentro del mensaje, pero prepara el borrador para el comercial de Anka que lo reenvió. Cambiarla a `original_sender` solo debe hacerse después de validar el enrutamiento con suficientes casos reales.

## Activar modo API

Usa proveedor API. Actualmente el bot soporta OpenAI API.

```powershell
.\.venv\Scripts\python.exe app.py set-mode --active-mode api
```

Antes de correrlo, verifica que exista `OPENAI_API_KEY` en el entorno.

## Activar modo ChatGPT Work / Plus

Usa ChatGPT Work/Plus con conectores y la skill `anka-cotizaciones-correo`.

```powershell
.\.venv\Scripts\python.exe app.py set-mode --active-mode chatgpt_work
```

En este modo el bot local bloquea `run/watch` para evitar duplicados. La tarea de ChatGPT debe ser el unico orquestador activo.

## Skill de decision

La skill `skills/anka-cotizaciones-correo` contiene las reglas comunes para:

- detectar solicitudes de cotizacion;
- extraer datos del correo;
- armar tabla de actividades;
- detectar faltantes;
- redactar borradores seguros;
- decidir si el caso requiere revision, preguntas al cliente o carpeta de Drive.

La misma logica debe guiar el bot local, el modo API y el modo ChatGPT Work.

## Adjuntos

El bot extrae texto básico de TXT, CSV, JSON, XML, KML, KMZ, PDF, DOCX y XLSX. Las imágenes incrustadas mediante `Content-ID` o disposición `inline` se consideran elementos de firma y no bloquean el análisis. Los planos DWG/DXF permanecen en revisión manual. Si un archivo técnico no puede leerse, la ficha registra qué archivo falló, por qué y qué dato obligatorio queda bloqueado.

Una referencia a trabajos anteriores sin su alcance adjunto se registra como dependencia pendiente. Las cifras o requisitos incompatibles entre fuentes se registran como contradicciones. Ambos casos impiden crear automáticamente la carpeta del proyecto hasta que comercial o técnica los resuelva.
