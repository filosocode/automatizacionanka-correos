# Registro de decisiones

Una línea por decisión: qué se decidió, por qué y qué se descartó. Orden cronológico inverso. Las decisiones que
cambian una regla también cambian `reglas_operativas.md` y suben su versión.

| Id | Fecha | Decisión | Por qué | Se descartó |
|---|---|---|---|---|
| D-14 | 2026-10-01 | Adoptar desarrollo guiado por especificaciones: `spec/` como fuente de verdad, requisitos con id, etiquetas `Cubre:` y `check_spec.py` en el CI. | Los fallos del piloto nacieron donde el comportamiento no estaba escrito (estados, contrato con Work). | Herramientas de BDD o generadores: pesan más de lo que aportan. |
| D-13 | 2026-10-01 | El contrato con Work es un esquema JSON validado por el puente, con un validador propio. | Un estado sin tildes dejó sin importar un borrador; el contrato debe ser ejecutable. | Añadir la dependencia `jsonschema` (el CI instala con hashes). |
| D-12 | 2026-10-01 | Un solo clic produce un correo: botón bloqueado, caso que lo dice y rechazo del mismo texto en 15 minutos. | Un envío doble real al cliente de prueba. | Solo mensajes de éxito. |
| D-11 | 2026-10-01 | Recordatorios automáticos como única excepción a R8 (R13), opcionales por caso, máx. 2, horario hábil de Colombia, texto fijo, solo al contacto registrado. | Los casos que se enfrían sin aviso son la pérdida más probable. | Automático por defecto; tres correos; recordar a las pocas horas. |
| D-10 | 2026-10-01 | El estado de las actividades es uno de tres valores y `confirmado` exige todos los datos esenciales (R12). | La tabla marcaba «confirmado» antes de que el cliente diera los datos. | Texto libre en el estado. |
| D-09 | 2026-10-01 | Con T asignada, la tabla solo cambia por el contacto registrado citando el código (R11); lo demás queda como propuesta. | Evitar que cualquier persona de la empresa modifique el alcance y luego se desentienda. | Código como contraseña; aceptar cambios de cualquiera de la misma empresa. |
| D-08 | 2026-10-01 | La T la asigna una persona (con número sugerido); Work y Python nunca la generan. | El consecutivo oficial vive en otro sistema (Motor Anka). | Generarla automáticamente. |
| D-07 | 2026-10-01 | El historial es evidencia: encadenado con huellas, sin borrarse con el caso, con copia `.eml` de los mensajes posteriores a la T. | Que nadie pueda negar quién pidió qué ni cuándo. | Borrado en cascada; solo extractos. |
| D-06 | 2026-10-01 | El puente acepta el estado de Work sin tildes ni mayúsculas. | Work lo escribió sin tildes y el caso no se importó. | Exigir texto exacto. |
| D-05 | 2026-10-01 | Un caso de Work con dependencias pendientes pasa a revisión técnica. | Paridad con el piloto; el caso quedaba bloqueado sin salida. | Dejar el estado que dijo Work. |
| D-04 | 2026-10-01 | El token CSRF se guarda y sobrevive a los reinicios. | El lanzador reinicia el dashboard al cambiar código y las páginas abiertas fallaban. | Token nuevo en cada arranque. |
| D-03 | 2026-10-01 | El dashboard es local; para uso remoto se evaluó una red privada (Tailscale) y se dejó para después de validar el flujo. | Datos sensibles; no exponer algo que aún cambia. | Hosting gratuito en la nube; túnel público. |
| D-02 | 2026-10-01 | Un lanzador único mantiene dashboard y puente, y hace copias diarias. | Procesos viejos y duplicados causaron los primeros fallos. | Abrir ventanas a mano. |
| D-01 | 2026-10-01 | En modo Work el código Python no lee Gmail: Work deja borradores con JSON y un puente de solo lectura los importa. | Evitar dos orquestadores sobre la misma cuenta. | Que Python y Work lean a la vez. |
