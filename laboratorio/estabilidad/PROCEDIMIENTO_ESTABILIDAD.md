# Estabilidad del supervisor (T-97) — PREPARADO, NO INSTALADO

Nada de lo de aquí está registrado en Windows ni modifica `anka/`. Registrar la tarea exige una autorización expresa (§5).

## 1. Qué se sabe de las caídas (hechos frente a hipótesis)

| Dato | Caída 1 | Caída 2 |
|---|---|---|
| Último éxito del receptor | 22/10 03:36:48 | 22/10 08:52:06 |
| Duración de esa ejecución | desde el 21/10 19:01:35 (≈ 8,6 h) | desde el 22/10 03:51:25 (≈ 5 h) |
| Línea de cierre en `anka.log` («Anka detenido») | **ninguna** | **ninguna** |
| Traza o error en `anka.log` | ninguno | ninguno |
| Cierre del propio Python (`Application Error`, 1000/1001) | ninguno | ninguno |
| Reinicio del equipo | no (arrancó el 14/10 20:34) | no |

**Demostrado:** el lanzador no salió por Ctrl+C (ese camino escribe «Anka detenido»), no fue un fallo nativo de Python, ni un reinicio de Windows, ni un cambio de código (ese camino también deja línea). Los dos procesos desaparecieron entre el último éxito del receptor y el siguiente sondeo (≤ 60 s).

**No demostrado (hipótesis, en este orden de plausibilidad):**
1. *Cierre desde fuera:* se cerró la ventana de «Iniciar Anka.bat», se cerró la sesión o algo terminó el árbol de procesos. Encaja con que no quede ninguna línea.
2. *Excepción no capturada* en el bucle de `servicio()` (`anka/cli/supervisor.py`): solo se captura `KeyboardInterrupt`; cualquier otra excepción termina el servicio, y el guardián devuelve el código **sin escribirlo en el registro**. La traza quedaría solo en la consola, que se pierde si la ventana se cierra.
3. *Otro arranque de ANKA* (`stop_old_instances` cierra a los anteriores: gana el último).
**Descartada como causa:** RestartManager. Sus avisos («se requiere reiniciar», Word no reiniciable) ocurrieron a las 03:45 y a las 08:46, es decir 9 min **después** de la caída 1 y 6 min **antes** de la caída 2; solo consultan, no terminan procesos.

El registro de ANKA no permite saber más; por eso la solución primero deja **evidencia** de cada salida.

## 2. Solución propuesta (mínima y nativa)

Un proceso residente pequeño, `laboratorio/estabilidad/vigilante.py`, lanzado por el Programador de tareas, que:

- arranca el lanzador existente (`supervisor.py`) sin ventana y **lo espera**; su stdout se descarta (la consola de ANKA puede llevar datos de clientes) y su stderr (trazas) va a `logs/supervisor_stderr.log`;
- cuando el lanzador termina, escribe en `logs/vigilante.log`: hora, duración, código de salida **clasificado** (cierre desde fuera `0xC000013A`, error no capturado `1`, normal `0`, «no reiniciar» `2`, otros), la última actividad de ANKA (receptor y ciclo) y si el equipo se reinició después; y lo relanza con espera creciente (5, 10, … hasta 60 s);
- no arranca nada si ya ve otro `supervisor.py` vivo (no hay pelea con «Iniciar Anka.bat»);
- una sola instancia del vigilante (candado del sistema operativo);
- **tormenta:** 4 arranques en 15 min → deja `ALERTA_ANKA.txt`, espera 5 min y vuelve a intentar (nunca gira sin descanso);
- **parada deliberada:** `detener` crea `DETENIDO.flag`, cierra los lanzadores y el vigilante no arranca nada mientras exista; `reanudar` la retira.

La tarea (`instalar_tarea.ps1`): usuario actual, sin privilegios elevados, disparada **al iniciar sesión y cada 5 min** (si el vigilante murió vuelve; si vive, `IgnoreNew`), sin límite de duración, 3 reintentos por minuto. Reutiliza todo el arranque y la limpieza de instancias que ya existen.

Orden de uso:
```
python -m laboratorio.estabilidad.vigilante estado     # solo lectura
python -m laboratorio.estabilidad.vigilante detener    # parada deliberada (crea DETENIDO.flag)
python -m laboratorio.estabilidad.vigilante reanudar   # retira la bandera
```
Una parada con Ctrl+C **no** es deliberada para el vigilante: ANKA vuelve en ≤ 1 min. Para parar de verdad, usar `detener`.

## 3. Archivos preparados
`vigilante.py`, `test_vigilante.py` (17 pruebas con procesos y relojes simulados), `instalar_tarea.ps1` y `desinstalar_tarea.ps1` (vista previa por defecto; cambian algo solo con `-Confirmar`), este documento.

## 4. Riesgos de la solución
- Es una pieza nueva en el camino crítico: si falla, ANKA no arranca. Mitigación: se prueba antes de depender de ella y «Iniciar Anka.bat» sigue funcionando (el vigilante no pelea con él).
- Con la tarea instalada, **no usar «Iniciar Anka.bat» a la vez**: su arranque cierra al lanzador anterior y el vigilante lo relanzaría (un solo ciclo de ida y vuelta, sin bucle por la espera creciente).
- Si la causa real es el cierre de sesión del usuario, la tarea (modo interactivo) también se detiene al cerrar sesión; el vigilante lo registrará, y la alternativa sería «Ejecutar tanto si el usuario inició sesión como si no», que requiere la contraseña de Windows (decisión del dueño).
- Un lanzador colgado pero vivo no se detecta (el vigilante lo ve «corriendo»). Ya existe `verificar_post_t83.ps1` para eso.
- El archivo `supervisor_stderr.log` puede contener direcciones de correo si una traza las cita; queda en la carpeta de datos del usuario y no se versiona.

## 5. Autorizaciones necesarias para instalarla
1. AUT-TAREA: registrar «ANKA Vigilante» en el Programador de tareas del usuario (`instalar_tarea.ps1 -Confirmar`).
2. AUT-REINICIO-PRUEBA: una prueba de recuperación terminando el lanzador una vez (§6), con la coordinación sin trabajos `publicando` y sin ventana C1/C2/C3 abierta.
No se toca el arranque de Windows, `config.json` ni el código de `anka/`.

## 6. Prueba mínima de recuperación (tras la autorización)
1. `verificar_post_t83.ps1` en verde y PENDIENTES vacía.
2. `instalar_tarea.ps1 -Confirmar`; esperar ≤ 1 min; `vigilante estado` muestra un lanzador; `Get-ScheduledTask 'ANKA Vigilante'` en «En ejecución».
3. Terminar solo el árbol del lanzador con `taskkill /PID <pid> /T /F`.
4. En ≤ 90 s: nuevo lanzador, línea en `vigilante.log` con la causa observable, y `verificar_post_t83.ps1` en verde otra vez.
5. `vigilante detener` → el lanzador se cierra y **no** vuelve en 6 min; `reanudar` → vuelve en segundos.
**Reversión:** `desinstalar_tarea.ps1 -Confirmar`, `vigilante detener` y arrancar con «Iniciar Anka.bat» como hasta ahora.

## 7. T-97B · Transición controlada del arranque manual al vigilante

**Cambios tras la revisión (T-97B):** (a) el vigilante reconocía «un lanzador» por una subcadena y habría dado por vivo cualquier proceso de Python que solo
mencionara `supervisor.py` (p. ej. `ruff check supervisor.py`), con lo que no habría arrancado ANKA; y `detener` los habría cerrado. Ahora compara palabra por
palabra: el ejecutable debe ser Python y un argumento debe llamarse `supervisor.py`; `-c` y `-m` se descartan. (b) con otro lanzador vivo consultaba los
procesos cada 2 s; ahora cada 30 s. Se comprobó en el sistema real con un proceso inofensivo (visto el árbol del lanzador, intérprete del entorno y
su hijo; nada más) y con 19 pruebas simuladas.

**Estado verificado para instalar:** el vigilante está LISTO_PARA_INSTALAR **si ANKA está corriendo** (§7.1 exige verificarlo justo antes).

### 7.1 Instalación sin interrumpir (el operador deja su consola abierta)
1. `verificar_post_t83.ps1` en verde; PENDIENTES vacía; sin trabajos `publicando`, sin pausa de C1/C2/C3 abierta.
2. `instalar_tarea.ps1` (vista previa) y leerla; después `instalar_tarea.ps1 -Confirmar`.
3. En ≤ 1 min la tarea arranca el vigilante, que ve el lanzador manual y **no arranca otro**: `vigilante.log` dice «hay otro lanzador de ANKA vivo».
   Comprobar: `vigilante estado` (1 grupo de lanzadores: la consola del operador) y `verificar_post_t83.ps1` sin cambios.
4. Mientras la consola siga abierta, ANKA la sostiene la consola; el vigilante es una red de seguridad que toma el relevo en ≤ 30 s si el lanzador muere.
5. Traspaso definitivo, cuando se quiera: cerrar la consola con Ctrl+C (o `vigilante detener` + `vigilante reanudar`); en ≤ 40 s el vigilante arranca ANKA sin ventana
   y desde entonces ya no depende de ninguna consola. Verificar con `verificar_post_t83.ps1`.

### 7.2 Riesgos con la consola del operador abierta
- Dos lanzadores a la vez no ocurre por el vigilante (mira antes de arrancar). Sí puede ocurrir si el operador pulsa otra vez «Iniciar Anka.bat» con el vigilante ya al mando:
  el nuevo lanzador cierra al anterior (comportamiento original) y el vigilante, al verlo vivo, no interviene; no hay bucle.
- Cerrar la consola con la X o cerrar sesión mata al lanzador manual: el vigilante lo anota («cerrada desde fuera») y lo relanza; si la sesión de Windows se cierra, la tarea
  interactiva también se detiene (ver riesgo de §4).
- Un `Ctrl+C` en la consola no cuenta como parada deliberada: ANKA vuelve en ≤ 40 s. Para parar de verdad, `detener`.

### 7.3 Reversión
1. `python -m laboratorio.estabilidad.vigilante detener` (crea la bandera, cierra los lanzadores; el vigilante no reinicia nada).
2. `desinstalar_tarea.ps1 -Confirmar` (si el vigilante había arrancado a ANKA, esto también cierra a ANKA: es esperado).
3. `python -m laboratorio.estabilidad.vigilante reanudar` (retira la bandera y `ALERTA_ANKA.txt`) y arrancar con «Iniciar Anka.bat» como antes.
4. `verificar_post_t83.ps1` en verde. No quedan cambios en `config.json`, Gmail, Drive ni SQLite.

### 7.4 Datos sensibles en los registros
- `vigilante.log`: solo horas, PIDs, duración, código de salida y las fechas de la última actividad; nada de correos ni clientes. Rota a 1 MB.
- `supervisor_stderr.log`: solo el flujo de errores del lanzador (trazas). La consola de ANKA (donde van los datos marcados como sensibles) se **descarta**.
  Una traza podría citar una dirección de correo dentro de un mensaje de excepción; el archivo vive en la carpeta de datos del usuario y no se versiona. Si se prefiere
  cero riesgo, se puede filtrar al rotar (mejora P2).
- `ALERTA_ANKA.txt` y `DETENIDO.flag`: una línea con la hora.
