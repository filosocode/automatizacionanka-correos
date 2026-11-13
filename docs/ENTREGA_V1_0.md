# ANKA V1.0 · Producción supervisada — documento de entrega

Fecha de corte: 22/10/2026 (entrega 23/10/2026, 10:00 a. m., America/Bogota). Idioma y reglas: ver `CLAUDE.md` y `spec/`.
Este documento describe el estado real y sus límites. No cambia el comportamiento de ANKA.

## 1. Qué es V1.0 y qué no es
- Es una **producción supervisada**: ANKA recibe solicitudes, ChatGPT Work las analiza, ANKA importa el borrador al dashboard y **una persona
  decide**. Nada sale al cliente sin revisión humana (R8). No cotiza.
- **No es** un análisis documental garantizado de PDF escaneados: ver §3.

## 2. Matriz de aceptación (resumen)
| Componente | Estado | Evidencia |
|---|---|---|
| Recepción Gmail, SQLite, coordinación, conciliación y archivado | Aceptado | C1, C2 y C3 recorrieron todo el flujo sin intervención; BD íntegra, sin duplicados |
| Pausa controlada y restauración (≤ 15 min) | Aceptado | C2: pausa de 2,6 min, hoja única, `config.json` restaurado con el mismo SHA-256 |
| Cancelación preventiva y lápidas | Aceptado | pruebas T-79…T-84; 1 cancelación registrada y consistente |
| Vigilante residente | Aceptado con reservas | recuperó la caída de las 10:07 en 14 s; no probado con sesión cerrada ni reinicio |
| Caso C1 (documental) | Aprobado con reservas | evaluación T-105 |
| Caso C3 (documental) | Aprobado con reservas | evaluación T-105 |
| Caso C2 (documental) | **NO aprobado** | Work no pudo leer el PDF escaneado: 0 de 4 actividades; no inventó nada |
| Revisión visual humana de C2 (V1–V3) | **Pendiente** | no existe `revision_visual_humana.md`; la plantilla está en `laboratorio/banco_documental/ejecucion_c1/` |
| Protección de C2 en el dashboard (T-110, D-181) | Aceptado | «Lectura pendiente», sin «Con esto alcanza» y rechazo en el servidor; 20 pruebas |
| Pantalla «Desempeño del análisis» (T-112) | Aceptado | `/desempeno` responde 200 (antes 500) |
| Calidad (`check.bat`) | Verde | 3 044 pruebas OK, 166 requisitos con prueba, cobertura 94 % |

## 3. Limitaciones conocidas
1. **PDF escaneados:** Work puede no ver las imágenes. Entonces devuelve una actividad sin cantidad, `status: bloqueado` y «Requiere revisión técnica
   interna». Un caso así **no está listo para cotizar**: el dashboard lo frena con «Lectura pendiente», no deja confirmar «Con esto alcanza»
   (ni por petición directa) hasta que una persona pulse «Ya revisé estos archivos yo» y la tabla tenga una cantidad real; la acción segura es
   leer el PDF a mano o pedir al cliente un PDF con texto. La revisión es una declaración de la persona, no una prueba de que leyó el archivo.
2. **Plan V1.1 (no implementado):** hoja `TEXTO_OCR_AUXILIAR` en el libro del trabajo, con el OCR local (probado: A–D y restricciones legibles; no distingue
   tachaduras, zonas rayadas ni figuras). Requiere prompt nuevo, contrato y un trabajo C2 nuevo.
3. **El vigilante no garantiza operación con la sesión de Windows cerrada** (la tarea es interactiva). Dejar la sesión iniciada y el equipo encendido.
4. **Intérprete Python en `Temp`:** `.venv\pyvenv.cfg` apunta a `...\AppData\Local\Temp\uv-python\cpython-3.12...`. Si Windows limpia `Temp`
   (Storage Sense / Silent Cleanup), ANKA no arranca. Mitigación sin reconstruir: copiar esa carpeta a un lugar fijo (p. ej. `D:\ANKA_runtime\python-3.12`) y,
   solo si `Temp` se limpia, cambiar `home =` en `.venv\pyvenv.cfg` a la copia. Reconstrucción definitiva: V1.1, con autorización.
5. **Alerta histórica:** «lleva más de 30 min publicado sin respuesta» (22/10 11:12) hace que `verificar_post_t83.ps1` marque 1 FALLA. No es un problema actual.
6. **Notificaciones «[Task Update]…»** de ChatGPT quedan como `pendiente_de_interpretacion_work` (ruido conocido).
7. **Cuota de Gmail:** evitar consultas manuales repetidas durante las ejecuciones horarias de Work.

## 4. Operación
- **Inicio:** `Iniciar Anka.bat` (o la tarea «ANKA Vigilante», que lo relanza si cae). Dashboard: `http://127.0.0.1:8765`.
- **Estado:** `.venv\Scripts\python.exe -X utf8 -m laboratorio.estabilidad.vigilante estado` y `powershell -File laboratorio\banco_documental\respaldo_t83\verificar_post_t83.ps1`.
- **Parada ordenada:** `... -m laboratorio.estabilidad.vigilante detener` y luego cerrar el lanzador. Reanudar: `... vigilante reanudar`.
- **Si cae:** el vigilante la relanza en ≤ 5 min. Si no, ver `%LOCALAPPDATA%\AnkaCorreos\logs\vigilante.log` y `supervisor_stderr.log`.
- **Copia de seguridad:** automática diaria (~15:14) local y en Drive «Anka respaldos». Restaurar: `python -m anka.infra.backup --restore ZIP` con el lanzador cerrado
  (no pisa `config.json`; ver `docs/MODO_OPERACION.md`).
- **Nunca** versionar ni imprimir `credentials.json`, `token.json`, `config.json`, `.env`, `*.sqlite3`, `csrf_token`.

## 5. Revisión humana obligatoria
- Antes de «Confirmar que alcanza» o «Preparar el correo»: leer la **Actividades**, **Archivos** y la nota de lectura. Si un archivo dice «No pudimos leer…»,
  el caso **no alcanza** hasta que una persona lea el original.
- Un borrador de Work nunca se envía tal cual. Si el texto sugerido dice «Fin del borrador interno. No enviar.», **no usarlo**.
- Revisión visual de C2 (V1–V3): la hace una persona distinta de quien preparó el banco, **antes** de ver el borrador de Work. **Sigue pendiente:**
  no se simuló ni se asignó aprobación.
- Esto es una **entrega supervisada**, no una producción autónoma: una persona revisa cada caso antes de que algo salga al cliente.

## 6. Versión y trazabilidad
Rama `integracion/ux-backend`, base `41245d5`. Commits locales de T-113 (sin push): ver `git log --oneline 41245d5..HEAD`.
1. cancelación de mensajes de prueba con lápida y recuperación segura (T-79 a T-84);
2. banco de evaluación documental y evaluador inverso del laboratorio (T-71 a T-77, T-81, T-103, T-105);
3. vigilante residente y controlador de ejecución integrada C1, C2 y C3;
4. suficiencia documental y respuestas internas (T-110, D-181);
5. corrección de `/desempeno` (T-112);
6. este documento y las exclusiones de entrega (`.gitignore`).
