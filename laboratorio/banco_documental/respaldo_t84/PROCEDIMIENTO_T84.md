# Despliegue del indicador de recepción (T-84) — PROCEDIMIENTO, NO EJECUTADO

Parche: `laboratorio/banco_documental/parche_t82/indicador_recepcion_t84.diff` (SHA-256 `09f4414723cb66636390da0f2b1711418db8b364cb49a926ed2922090ce346f5`).
Copia de seguridad verificable: esta carpeta (`*.orig`, `*.copia`, `SHA256SUMS.txt`). Verificación posterior: `verificar_post_t84.ps1` (solo lectura).
**No usar `cancelacion_t83.diff`**: choca con este parche en `spec/requisitos.md` y `spec/05-decisiones.md` (ver «Riesgos»).

## Qué reinicia a ANKA y qué no
El lanzador compara cada 3 s las fechas de `*.py` de la raíz, `anka/**/*.py` y `anka/web/templates/*`. Este parche solo toca **un** archivo vigilado
(`anka/infra/work_heartbeat.py`), así que provoca **un** reinicio: espera hasta 20 s a que terminen las acciones del dashboard y los envíos,
detiene a los hijos y el guardián (la ventana de «Iniciar Anka.bat») lanza el servicio con el código nuevo. Dura unos segundos; el dashboard
queda sin respuesta en ese lapso. `spec/` y `tests/` no se vigilan. Si el código nuevo no importa, el servicio sale con un código distinto de 75 y el guardián
**termina** (ANKA queda detenido y la ventana en pausa): por eso existe la reversión.

## Ventana de intervención (≈ 5 minutos)
- La coordinación corre cada 300 s (candado `work_coordinacion.lock`) y el receptor cada 60 s. Aplicar **1 a 2 minutos después** de que el candado cambió de hora
  (así el ciclo ya terminó y el siguiente queda a ~3 minutos), y fuera de un sondeo (el receptor toca `recepcion_control` en el segundo :45 aproximado).
- Antes: sin acciones del dashboard abiertas, sin envíos en curso y ningún trabajo pendiente o publicando (hoy: 8 conciliados). `work_dispatch_enabled` y
  `work_drive_enabled` están en `false`, por lo que un corte no deja nada a medias.
- Cierra el dashboard en el navegador si hay un formulario sin enviar (la página se recarga sola al cambiar el código solo si no hay campos editados).

## Antes de aplicar (todo de solo lectura)
```powershell
cd D:\Automatizacion_Correos
git apply --check -p1 --whitespace=nowarn laboratorio\banco_documental\parche_t82\indicador_recepcion_t84.diff   # debe terminar sin salida
(Get-FileHash laboratorio\banco_documental\parche_t82\indicador_recepcion_t84.diff -Algorithm SHA256).Hash       # = 09F44147...346F5
cd laboratorio\banco_documental\respaldo_t84; sha256sum -c SHA256SUMS.txt                                          # (Git Bash) todo OK
powershell -NoProfile -File .\verificar_post_t84.ps1                                                               # estado de partida: solo falla el indicador
```
Si `--check` falla o algún originales ya no coincide con `SHA256SUMS.txt`, **no aplicar**: alguien cambió esos archivos.

## Aplicación
```powershell
cd D:\Automatizacion_Correos
git apply -p1 --whitespace=nowarn laboratorio\banco_documental\parche_t82\indicador_recepcion_t84.diff
```
Con `core.autocrlf=true` (como en esta máquina) conserva los finales CRLF de los archivos; se probó en una copia: los 4 archivos quedaron 100 % CRLF y con el
mismo contenido validado en T-82F.
Después, sin tocar nada más: esperar ~30 s al reinicio automático (en `logs\anka.log`: «Cambió el código…», «El lanzador se vuelve a lanzar…», «Anka iniciado») y **recargar el dashboard**.

## Pruebas posteriores (solo lectura)
```powershell
powershell -NoProfile -File laboratorio\banco_documental\respaldo_t84\verificar_post_t84.ps1
```
Debe dar `TODO EN ORDEN` (código de salida 0): una sola instancia (1 lanzador + 1 servicio), `/state` con HTTP 200, `bridge=ok` y texto sin «Puente Work»,
`recepcion_control` con éxito de menos de 180 s y 0 errores, base íntegra y 0 duplicados, candado de coordinación posterior al reinicio (puede tardar hasta 5 minutos:
repetir el script pasado ese tiempo), y ningún WARNING/ERROR nuevo en el registro. Además, a ojo: el chip del dashboard dice «Revisión de correos: activa».

## Reversión
Disparadores: el servicio no vuelve a arrancar en 1 minuto, el script falla en algo distinto del plazo de la coordinación, o aparece un error nuevo en el registro.
1. Restaurar los tres originales (**un solo** archivo vigilado cambia, por tanto un solo reinicio):
```powershell
cd D:\Automatizacion_Correos
Copy-Item laboratorio\banco_documental\respaldo_t84\requisitos.md.orig    spec\requisitos.md    -Force
Copy-Item laboratorio\banco_documental\respaldo_t84\05-decisiones.md.orig spec\05-decisiones.md -Force
Remove-Item tests\test_indicador_recepcion.py
Copy-Item laboratorio\banco_documental\respaldo_t84\work_heartbeat.py.orig anka\infra\work_heartbeat.py -Force   # este es el que dispara el reinicio
```
   (Alternativa equivalente: `git apply -R -p1 --whitespace=nowarn <el diff>`.)
2. Si el guardián ya terminó (ventana de «Iniciar Anka.bat» en pausa), cerrar esa ventana y abrir de nuevo **«Iniciar Anka.bat»** desde el Explorador, no desde un segundo plano de Claude.
3. Confirmar la restauración: `sha256sum -c laboratorio/banco_documental/respaldo_t84/SHA256SUMS.txt` (los `.orig` frente a los archivos restaurados con `cmp`) y ejecutar el script de verificación: vuelve el
   único fallo previsto (el indicador mostrará «detenida», que es la situación anterior y no un error nuevo).
4. La base y `config.json` no se tocan en ningún paso, así que no hay nada que revertir en datos.

## Riesgos
- **Reinicio breve** (unos segundos) con el dashboard sin respuesta; los hilos del receptor y de la coordinación no se esperan, solo las acciones del dashboard y los envíos. Con el despacho y el preparador de Drive apagados y sin trabajos pendientes, un corte en pleno ciclo es repetible sin efectos.
- **Código que no importa → ANKA detenido hasta arrancarlo a mano.** Mitigado: el parche pasó ruff, mypy y 2959 pruebas en una copia, y el contenido aplicado coincide byte a byte (ignorando CR) con esa copia.
- **Conflicto con T-83**: ambos parches modifican `spec/requisitos.md` y `spec/05-decisiones.md` junto a la misma ancla. Si se aplica T-84, regenerar o fusionar a mano esos hunks de `spec/` de T-83 (los archivos de `anka/` de T-83 no chocan con T-84).
- **Cambios previos sin confirmar**: `spec/requisitos.md` ya tiene las secciones T-79 a T-82 sin versionar; el parche aplica sobre ese estado y el respaldo `.orig` lo conserva.
- **Cambia lo que ve el operador**: el chip pasa de «detenida» a «activa»; deja de mostrarse en el chip el estado `attention` del puente general (los análisis rechazados siguen en «Mis casos»).
