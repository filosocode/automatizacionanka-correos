# Propuesta: prompt de Work V6.2 (no aplicada)

**Estado: solo propuesta.** No cambia `docs/PROMPT_WORK_ANKA_V6.txt` ni la skill. Aplicarla exige la decisión del dueño, una
versión nueva del prompt, la prueba manual en Work (varios hilos reales o de prueba, incluidos un KML, un adjunto grande, uno
bloqueado y un hilo con respuesta citada) con fecha y versión, y las pruebas de abajo. Ninguna prueba automática garantiza cómo
lo interpreta ChatGPT.

**Por qué ahora es seguro hacerlo.** Desde D-106 (T-28, U-41) el puente tolera y avisa los descuidos secundarios, así que V6.2 ya
no tiene que ser perfecta para no perder el análisis: solo tiene que equivocarse menos. Mide antes y después los rechazos por
causa (la tabla `work_rejections` y la alerta de más de 3 al día lo permiten).

## Qué falla hoy en V6.1 (probado contra el puente)
- «omite lo desconocido **o pon «no indicado»**» rompía `size_bytes`, `owner`, `blocking`, `disposition`, `read_via` y
  `analysis_complete`. Ahora el puente los toma como desconocidos con aviso, pero conviene dejar de pedirlo.
- «todo en español salvo las claves» choca con los valores cerrados en inglés (`explicit`, `client`, `ask_client`). El puente ya
  traduce los más comunes con un mapa explícito; aun así el prompt debe decir los valores literales junto a cada campo.
- El extracto «literal» sin regla de escape: comillas, saltos de línea y XML rompen el JSON (esto sigue rechazándose si el JSON queda
  irrecuperable).
- Margen de 217 caracteres bajo el tope de 12 000 de `tests/test_prompt_work.py`: V6.2 debe recortar, no añadir.

## Diff propuesto (sobre V6.1; ilustrativo)

```diff
-PROMPT OPERATIVO — GRUPO ANKA — V6.1
+PROMPT OPERATIVO — GRUPO ANKA — V6.2

 2. LOS ADJUNTOS   (sin cambios; añadir al final:)
+Si el conector no muestra el tamaño exacto de un adjunto, OMITE size_bytes de ese adjunto y di por qué en reason. Nunca escribas «no indicado» ni un tamaño estimado.

-3. LEER BIEN  (5 líneas de reglas por formato)
+3. LEER BIEN  → se mueve a la skill (references/lectura_por_formato.md). Quedan 3 líneas: coverage «completa/parcial/no_legible», visual_interpretation=no_certificada por defecto, no deducir magnitudes/CRS/datum.
-4. HISTORIAL   (4 líneas)
+4. HISTORIAL   → 1 línea (R11): «Con código T asignado, solo cambia el alcance un mensaje posterior del contacto registrado que cite el código». El resto, a la skill.

 5. LA TABLA    (sin cambios, salvo:)
-- Cada archivo lleva en evidence el extracto LITERAL (excerpt)…, cópialo tal cual, sin parafrasear.
+- Si citas un extracto (evidence.excerpt): máximo 200 caracteres, tal cual en lo esencial, SIN saltos de línea. Cambia las comillas dobles por comillas simples. Si no tienes un extracto, no escribas esa fila de evidence.
```

Bloque nuevo, justo antes del ejemplo (reemplaza la línea «todo en español salvo las claves; omite lo desconocido o pon «no indicado»»):

```diff
+VALORES PERMITIDOS (cópialos EXACTOS, en minúscula y sin punto final; no los traduzcas)
+ status: uno de los diez estados de la lista, sin punto final.
+ estado (de cada fila): confirmado | pendiente | contradictorio
+ evidence_register.status: explicit | partial | absent | unreadable | contradictory | unconfirmed
+ evidence_register.owner: client | anka | contractor | unknown
+ evidence_register.blocking, analysis_complete: true | false
+ question_audit.disposition: ask_client | internal_review | do_not_ask
+ attachments.read_via: attachment | raw_mime        attachments.status: extraido | parcial | bloqueado
+ missing_data.type: critical | recommended
+
+TIPOS
+ Texto = entre comillas. Número (size_bytes) = entero sin comillas. true/false sin comillas.
+ Listas ([ ]) también con un solo elemento: "sources": ["TdR.pdf p. 2"], "cited_codes": ["T-123"].
+ Si un valor de TEXTO no aplica o no lo sabes: "" (cadena vacía). Si es número o true/false y no lo sabes: omite el campo.
+ Nunca null, nunca «no indicado», nunca un número donde va texto ni texto donde va número.
+ Dentro de cualquier texto: sin saltos de línea ni tabs; comillas simples en vez de dobles; sin barra invertida.
+ Los marcadores [ANKA_DASHBOARD_JSON_V1] y [/ANKA_DASHBOARD_JSON_V1] aparecen UNA vez cada uno: no los menciones en ningún otro texto.
```

```diff
-El JSON, con este formato (todo en español salvo las claves; omite lo desconocido o pon «no indicado»; ejemplo ficticio):
+El JSON tiene dos partes. NÚCLEO (siempre): thread_id, message_id, client_email, client_name, project_name, summary,
+received_at, status, engine, skill_version, activities (las diez columnas, todas texto), attachments (filename, size_bytes,
+status, reason, coverage). EXTRAS (opcionales; si no puedes escribirlos bien, OMITE el bloque entero, no lo rellenes):
+fields, missing_data, evidence_register, question_audit, evidence dentro de attachments, contradictions,
+scope_dependencies, related_threads, requested_by_*, cited_codes, model.
+Ejemplo ficticio (el ejemplo extendido incluye un segundo adjunto bloqueado y una contradicción con sources en lista):
```

```diff
-- Antes de guardar valida: JSON válido, estado de la lista, thread_id y message_id correctos. Si detectas que este prompt llegó cortado, registra revisión interna y no inventes lo que falte.
+LISTA DE COMPROBACIÓN (antes de guardar; si algo falla, corrígelo y vuelve a mirar)
+ 1. El texto interno empieza con [REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION], sin tilde.
+ 2. Hay un solo borrador del hilo y un solo bloque JSON, con los dos marcadores una vez.
+ 3. El JSON es puro: sin ``` ni comentarios, sin texto dentro del bloque, sin saltos de línea dentro de los textos, cerrado completo.
+ 4. thread_id es el del hilo; message_id es el del mensaje del cliente (no el del borrador); skill_version = PRECISO_V6.2.
+ 5. status es uno de los diez, sin punto; los valores cerrados están copiados de VALORES PERMITIDOS.
+ 6. Ningún null, ningún «no indicado» en números ni true/false; listas con corchetes aunque tengan un elemento.
+ 7. Hay un elemento de attachments por cada adjunto del hilo (nombre y tamaño; sin tamaño: omítelo y explica).
+ 8. Este prompt llegó entero: lo último que leíste es FIN_PROMPT_ANKA_V6.2. Si no, bloqueo y no inventes.
```

Se mantienen sin tocar: la sección NUNCA (R8, no inventar), la regla de R11, los estados de fila de R12, `analysis_complete`,
`ask_client` solo si bloquea y nadie ha preguntado, y el cierre. Presupuesto objetivo: 9 500 caracteres o menos.

## Lo que hay que hacer para aplicarla
1. Decidir con el dueño: acortar el extracto a 200 caracteres; permitir omitir los extras; si el conector de Gmail de Work entrega
   `size_bytes`; si el borrador se guarda como texto o HTML (con HTML, la regla de etiquetas del puente ya conserva un KML citado);
   qué hacer cuando Work reintenta tras un bloqueo (actualizar el mismo borrador o crear otro).
2. Pasar por `/clarificar-spec`: requisito nuevo (núcleo y extras) que acote T-39 y T-41, decisión nueva y cambios en
   `spec/03-contrato-work.md`.
3. Pruebas (`tests/test_prompt_work.py`): longitud menor que 9 500 y cierre `FIN_PROMPT_ANKA_V6.2`; el ejemplo valida y pasa por
   `sync.extract` sin correcciones; los valores de «VALORES PERMITIDOS» coinciden con los enumerados del esquema (derivados de él);
   el ejemplo no trae `null` ni «no indicado» en campos no textuales; están las 8 líneas de la lista de comprobación; el prompt
   conserva NUNCA, R11 y los tres estados de R12; ya no contiene «omite lo desconocido o pon «no indicado»». En
   `tests/test_version_de_work.py`, `PRECISO_V6.2` se verifica por adjuntos (T-41).
4. Actualizar `docs/INSTRUCCIONES.txt` (hay una prueba que lo exige), la skill y `docs/PILOTO_ACEPTACION.md`, y registrar la prueba
   manual en Work con fecha y versión.
