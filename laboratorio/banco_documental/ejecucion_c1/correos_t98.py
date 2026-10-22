"""Textos exactos de los correos ficticios de C2 y C3 (T-98). Se pegan tal cual al redactar el correo; nada de esto se ha enviado.

Los cuerpos piden la interpretación comercial completa (actividades, datos faltantes y preguntas al cliente) y NO dicen qué
contiene el adjunto ni qué falta: la evaluación es ciega. Aclaran que ninguna respuesta se enviará (R8).
"""

from __future__ import annotations

from laboratorio.banco_documental.ejecucion_c1 import c1

DESTINATARIO = c1.DESTINATARIO
REMITENTE = c1.REMITENTE

CUERPO_C2 = """Buen día.

Les escribo desde la Empresa de Servicios Ejemplo E.S.P. (ficticia) para solicitar cotización de los trabajos descritos en los términos de referencia adjuntos (tdr_02.pdf). El documento es un escaneado.

Por favor traten esta solicitud como si fuera real y hagan la interpretación comercial completa: la tabla de actividades con sus cantidades y unidades tal como están escritas, las restricciones del documento, los datos que falten o se contradigan para poder cotizar y las preguntas que habría que hacerle al cliente.

Es una prueba de laboratorio: no se debe enviar ninguna respuesta al cliente ni cotizar. Solo se espera el borrador de análisis para revisión interna.

Gracias.
Prueba de laboratorio Anka (ficticia)
"""

CUERPO_C3 = """Buen día.

Les escribo para solicitar cotización del proyecto vial ficticio cuyo resumen de cantidades va en el libro adjunto (cantidades_03.xlsx).

Por favor traten esta solicitud como si fuera real y hagan la interpretación comercial completa: la tabla de actividades con sus cantidades y unidades tal como están en el libro, las restricciones, los datos que falten o no cuadren para poder cotizar y las preguntas que habría que hacerle al cliente.

Es una prueba de laboratorio: no se debe enviar ninguna respuesta al cliente ni cotizar. Solo se espera el borrador de análisis para revisión interna.

Gracias.
Prueba de laboratorio Anka (ficticia)
"""

CORREOS: dict[str, tuple[str, str]] = {
    "C2": (c1.PERFILES["C2"].asunto, CUERPO_C2),
    "C3": (c1.PERFILES["C3"].asunto, CUERPO_C3),
}
