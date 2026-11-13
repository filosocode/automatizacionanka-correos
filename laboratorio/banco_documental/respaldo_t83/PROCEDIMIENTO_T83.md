# Despliegue de la cancelación segura (T-83) — PROCEDIMIENTO, NO EJECUTADO

Parche: `laboratorio/banco_documental/parche_t82/cancelacion_t83_v2.diff` (compatible con T-84 ya desplegado).
SHA-256: `d591838de8ab658e71f420a00b1bc6b49f486f6b138b597d9bc5788472b6a8a0`. **El diff original `cancelacion_t83.diff` (SHA-256 `f218f301…bb347`) se conserva intacto y no debe usarse.**
Copia de seguridad: `respaldo_t83/originales/` (los 15 archivos existentes que el parche modifica; los 4 que crea no tienen original) + `SHA256SUMS.txt`.
Verificación posterior de solo lectura: `verificar_post_t83.ps1`.

## Qué cambia (resumen)
- Nuevos: `anka/dominio/cancelacion_work.py`, `anka/infra/lapidas.py`, `anka/servicios/cancelaciones.py`, `tests/test_cancelacion_work.py`.
- Modificados en `anka/` (9): `work_import` (rechaza importar el borrador de un mensaje cancelado), `recepcion` (`conciliar_con_work` devuelve `cancelado`), `coordinacion`, `despacho`,
  `trabajos_work`, `estado_operativo`, `backup` (reaplica lápidas tras restaurar), `work_jobs` (órdenes `cancelar`, `verificar-lapidas`, `reaplicar-lapidas`) y `work_sync`.
- Spec y pruebas: `spec/requisitos.md` (T-83), `spec/05-decisiones.md` (D-179), `spec/06-arquitectura.md`, y 3 archivos de pruebas existentes.
- **No cambia**: `config.json`, SQLite (sin migración de esquema: `cancelado` ya existía), el contrato Work, PRECISO_V6.1.3 ni la tarea Work. Nada se cancela por sí solo:
  sin ejecutar `work_jobs cancelar --confirmar` el comportamiento para cualquier mensaje es idéntico al de hoy.

## Reinicio
Son 9 archivos vigilados, pero `git apply` los escribe en una fracción de segundo; el lanzador los ve juntos (revisa cada 3 s y espera 2 s) y reinicia **una vez**, como en T-84 (≈7 s).
Si cayera en dos revisiones seguidas habría dos reinicios seguidos; es inocuo. Si el código nuevo no importa, el guardián termina y ANKA queda detenido: ver la reversión.

## Ventana (≈ 5 minutos)
Igual que T-84: 1–2 minutos después de que `work_coordinacion.lock` cambie de hora (ciclo cada 300 s), sin acciones del dashboard ni envíos en curso,
y sin trabajos pendientes o publicando (hoy: 8 conciliados). Aplicar con ANKA en estado normal; no en medio de una cancelación o una restauración.

## Antes de aplicar (solo lectura; si algo falla, NO aplicar)
```powershell
cd D:\Automatizacion_Correos
(Get-FileHash laboratorio\banco_documental\parche_t82\cancelacion_t83_v2.diff -Algorithm SHA256).Hash   # = D591838D…A8A0
git apply --check -p1 --whitespace=nowarn laboratorio\banco_documental\parche_t82\cancelacion_t83_v2.diff
cd laboratorio\banco_documental\respaldo_t83; sha256sum -c SHA256SUMS.txt                                # (Git Bash) todo OK
cd D:\Automatizacion_Correos
powershell -NoProfile -File laboratorio\banco_documental\respaldo_t84\verificar_post_t84.ps1             # TODO EN ORDEN (T-84 intacto)
# originales == archivos vivos (si alguien cambió algo desde el respaldo, no aplicar):
Get-Content laboratorio\banco_documental\respaldo_t83\archivos_del_parche.txt | Where-Object { $_ -like '* EXISTE' } | ForEach-Object { $f = ($_ -split ' ')[0]; if ((Get-FileHash $f).Hash -ne (Get-FileHash "laboratorio\banco_documental\respaldo_t83\originales\$f").Hash) { "DISTINTO $f" } }
```

## Aplicación
```powershell
git apply -p1 --whitespace=nowarn laboratorio\banco_documental\parche_t82\cancelacion_t83_v2.diff
```
Esperar ~30 s al reinicio automático («Cambió el código…», «Anka iniciado» en `logs\anka.log`) y recargar el dashboard. Luego:
```powershell
powershell -NoProfile -File laboratorio\banco_documental\respaldo_t83\verificar_post_t83.ps1
```
Debe dar `TODO EN ORDEN`: los controles de T-84 (instancia única, HTTP 200, indicador «activa», receptor sano, base íntegra, 0 duplicados, sin errores nuevos, coordinación posterior al reinicio),
la corrección T-84 conservada, los módulos de cancelación presentes, `verificar-lapidas` con el diario y la base en orden, y 0 cancelaciones no pedidas.
Si solo falla el candado de coordinación, repetir pasados 5 minutos.

## Primer uso de la orden (aparte, solo con autorización expresa y con un mensaje ficticio)
No forma parte del despliegue. La orden exige `--confirmar`, `--etiqueta [PI-…]` autorizada y rechaza mensajes reales, publicados, ambiguos o conciliados:
`python -m anka.cli.work_jobs cancelar --thread-id … --message-id … --etiqueta [PI-…] --operador … --motivo … --confirmar`.

## Reversión
Disparadores: el servicio no vuelve a arrancar en 1 minuto, el dashboard no responde, `verificar_post_t83.ps1` falla en algo distinto del plazo de la coordinación, o hay un error nuevo atribuible.
1. Restaurar originales y quitar archivos nuevos (alternativa equivalente: `git apply -R -p1 --whitespace=nowarn <el diff v2>`):
```powershell
cd D:\Automatizacion_Correos
Get-Content laboratorio\banco_documental\respaldo_t83\archivos_del_parche.txt | ForEach-Object {
  $f, $t = $_ -split ' '
  if ($t -eq 'EXISTE') { Copy-Item "laboratorio\banco_documental\respaldo_t83\originales\$f" $f -Force } else { Remove-Item $f -Force }
}
```
2. Si el guardián ya terminó (ventana de «Iniciar Anka.bat» en pausa), abrir «Iniciar Anka.bat» desde el Explorador, no desde un segundo plano de Claude.
3. Confirmar con `verificar_post_t84.ps1` (T-84 debe seguir en orden: `work_heartbeat.py` no está en el parche) y con `sha256sum -c` de los originales.
4. **Lápidas ya creadas**: si se llegó a cancelar algo, el diario `%LOCALAPPDATA%\AnkaCorreos\cancelaciones.jsonl` y las filas `cancelado` de `trabajos_work` permanecen. El código anterior respeta el estado `cancelado`
   en la recepción, la creación y la publicación, pero **no** impide importar un borrador ni conciliar la fila (las rutas abiertas de T-82): no borrar nada, y no dejar activa la coordinación con un mensaje cancelado
   en vuelo sin una persona. Ninguna reversión toca trabajos, SQLite ni `config.json`.

## Tratamiento de lápidas tras restaurar una copia de seguridad
`backup.restore` reaplica solas las lápidas del diario que falten, sin migrar. Si la copia es anterior a `trabajos_work` se avisa y, después de migrar, se ejecuta
`python -m anka.cli.work_jobs reaplicar-lapidas --confirmar` y luego `verificar-lapidas` (debe dar `en_orden_total`). Los choques con trabajos avanzados se informan y nunca se pisan.

## Riesgos residuales
- Un trabajo publicado o en publicación no se cancela con la orden; exige a una persona y revisar Drive.
- Un borrador de Work ya en vuelo de un mensaje cancelado se descarta al importar y queda sin cierre del lado de Work.
- El diario es un archivo local: si se pierde junto con la base, no hay lápida que reaplicar.
- La orden depende de la etiqueta `[PI-…]` en el asunto del mensaje registrado.
- Quien edite `anka/` mientras ANKA corre provoca reinicios; aplicar siempre con la ventana indicada.
- `check.bat` completo sigue sin completarse en el repo real; la validación descansa en la suite completa sobre una copia con el parche aplicado.
