"""Los datos correctos de los tres casos del banco: de aquí salen los documentos y, aparte, las referencias (T-71).

Todo es ficticio: entidades, predios, municipios, códigos y cifras no corresponden a ningún cliente ni proyecto real.
Cada valor y cada unidad se guardan tal como están escritos en el documento («2,350», «ha»): no hay conversiones
implícitas. `valor` es la lectura numérica con punto decimal que un evaluador debe aceptar.

Este módulo NO se entrega al sistema evaluado: contiene las respuestas.
"""

from __future__ import annotations

from typing import Any

CASO_1 = "C1"
CASO_2 = "C2"
CASO_3 = "C3"

ARCHIVOS = {CASO_1: "solicitud_01.pdf", CASO_2: "tdr_02.pdf", CASO_3: "cantidades_03.xlsx"}
TIPOS = {CASO_1: "pdf_digital", CASO_2: "pdf_escaneado", CASO_3: "excel"}

Dato = dict[str, Any]


def _actividad(identificador: str, nombre: str, claves: tuple[str, ...], literal: str, valor: str, unidad: str,
               servicio: str, origen: Dato, entregable: str = "", claves_entregable: tuple[str, ...] = (),
               **extra: Any) -> Dato:
    return {"id": identificador, "nombre": nombre, "claves": list(claves), "cantidad_literal": literal,
            "valor": valor, "unidad_literal": unidad, "servicio": servicio, "origen": origen,
            "entregable": entregable, "claves_entregable": list(claves_entregable), **extra}


# --------------------------------------------------------------------------------------------- C1: PDF digital
C1: Dato = {
    "id": CASO_1,
    "titulo": "Solicitud de cotización de servicios de topografía",
    "parametros": {
        "entidad": "Constructora Ejemplo Andino S.A.S. (ficticia)", "codigo": "SC-0457-FIC", "fecha": "12 de noviembre de 2026",
        "proyecto": "Parque solar Los Almendros (proyecto ficticio)", "predio": "Lote Los Almendros",
        "ubicacion": "vereda La Esperanza, municipio de Villa Ejemplo, departamento de Cundinamarca",
        "area_predio": "21,0", "plazo_dias": "25", "reunion": "25 de noviembre de 2026", "fotos_por_punto": "12",
        "puntos_en_texto": "10", "nota_siete": "Nota 7", "precision": "±2 cm", "equidistancia": "0,5 m",
        "contacto": "Contacto Ejemplo (contacto@ejemplo.invalid)",
    },
    "actividades": [
        _actividad("C1-A1", "Levantamiento topográfico planimétrico y altimétrico del área de intervención",
                   ("planim", "altim"), "18,5", "18.5", "ha", "topografia",
                   {"documento": "solicitud_01.pdf", "pagina": 1, "tabla": 1, "fila": 1},
                   "Plano en DWG y PDF; curvas cada 0,5 m", ("dwg", "pdf")),
        _actividad("C1-A2", "Levantamiento de ejes de vías internas existentes", ("ejes", "vias"),
                   "2,350", "2.35", "km", "topografia",
                   {"documento": "solicitud_01.pdf", "pagina": 1, "tabla": 1, "fila": 2},
                   "Perfil longitudinal en PDF", ("perfil",), ambiguo=True, interpretaciones=["2.35", "2350"]),
        _actividad("C1-A3", "Localización y monumentación de puntos de control", ("monument", "control"),
                   "12", "12", "puntos", "geodesia",
                   {"documento": "solicitud_01.pdf", "pagina": 1, "tabla": 1, "fila": 3},
                   "Reseñas en PDF", ("resen",)),
        _actividad("C1-A4", "Levantamiento de construcciones existentes", ("construc",),
                   "7", "7", "construcciones", "topografia",
                   {"documento": "solicitud_01.pdf", "pagina": 1, "tabla": 1, "fila": 4},
                   "Planta y fachada en DWG", ("planta", "fachada")),
        _actividad("C1-A5", "Localización de quebradas y drenajes", ("quebr", "drenaj"),
                   "3", "3", "quebradas", "topografia",
                   {"documento": "solicitud_01.pdf", "pagina": 1, "tabla": 1, "fila": 5},
                   "Trazado en DWG", ("dwg",)),
    ],
    "restricciones": [
        {"id": "C1-R1", "texto": "Plazo de entrega: 25 días calendario desde la orden de inicio.",
         "claves": ["25", "dias"], "critica": True, "origen": {"documento": "solicitud_01.pdf", "pagina": 2}},
        {"id": "C1-R2", "texto": "Acceso al predio de lunes a viernes entre 7:00 y 16:00; sábados solo con autorización.",
         "claves": ["lunes", "viernes"], "critica": False, "origen": {"documento": "solicitud_01.pdf", "pagina": 2}},
        {"id": "C1-R3", "texto": "Precisión horizontal requerida: ±2 cm (posicionamiento GNSS).",
         "claves": ["2 cm", "gnss"], "critica": False, "origen": {"documento": "solicitud_01.pdf", "pagina": 2}},
        {"id": "C1-R4", "texto": "El predio tiene presencia de ganado; se exige afiliación vigente a ARL y EPP.",
         "claves": ["arl", "ganado"], "critica": False, "origen": {"documento": "solicitud_01.pdf", "pagina": 2}},
    ],
    "contradicciones": [
        {"id": "C1-K1", "tema": "cantidad de puntos de control", "critica": True,
         "valores": [{"literal": "12", "unidad": "puntos", "origen": {"documento": "solicitud_01.pdf", "pagina": 1,
                                                                      "tabla": 1, "fila": 3}},
                     {"literal": "10", "unidad": "puntos", "origen": {"documento": "solicitud_01.pdf", "pagina": 2,
                                                                      "parrafo": "Condiciones, quinto punto"}}],
         "literales_clave": ["12", "10"], "palabras": ["control"],
         "motivo": "La tabla pide 12 puntos de control y el texto de condiciones estima 10."},
    ],
    "faltantes": [
        {"id": "C1-F1", "tema": "ubicacion", "bloqueante": True, "destino": "cliente",
         "palabras": ["poligono", "kmz", "limites del predio", "ubicacion del predio"],
         "motivo": "El polígono del predio «se enviará por separado» y no está en el documento."},
        {"id": "C1-F2", "tema": "sistema", "bloqueante": False, "destino": "cliente",
         "palabras": ["sistema de coordenadas", "datum", "magna", "origen"],
         "motivo": "La nota 6 dice que el sistema de coordenadas se definirá en la reunión de inicio; puede proponerse uno."},
        {"id": "C1-F3", "tema": "cantidad de puntos", "bloqueante": False, "destino": "cliente",
         "palabras": ["10", "12", "puntos"],
         "motivo": "Resolver la contradicción entre 12 y 10 puntos de control."},
    ],
    "revisiones_internas": [
        {"id": "C1-I1", "texto": "Comprobar que el área de intervención (18,5 ha) cabe en el predio (21,0 ha).",
         "palabras": ["cabe", "predio"]},
        {"id": "C1-I2", "texto": "Decidir la lectura de «2,350 km»: 2,35 km por separador decimal.",
         "palabras": ["2,350", "separador"]},
    ],
    "datos_presentes": [
        {"tema": "plazo", "palabras": ["plazo"]}, {"tema": "entregables", "palabras": ["entregable", "formato"]},
        {"tema": "precision", "palabras": ["precision"]}, {"tema": "acceso", "palabras": ["acceso", "horario"]},
    ],
    "trampas": [
        {"id": "C1-T1", "valor_literal": "12", "valor": "12", "origen": {"documento": "solicitud_01.pdf", "pagina": 2},
         "es": "12 fotografías por punto de control", "confusion": "tomarlo como cantidad de puntos de control",
         "conceptos_confundibles": ["monument", "control"]},
        {"id": "C1-T2", "valor_literal": "21,0", "valor": "21.0", "origen": {"documento": "solicitud_01.pdf", "pagina": 1},
         "es": "área total del predio según escritura", "confusion": "tomarlo como área de intervención",
         "conceptos_confundibles": ["planim", "altim"]},
        {"id": "C1-T3", "valor_literal": "25", "valor": "25", "origen": {"documento": "solicitud_01.pdf", "pagina": 2},
         "es": "día de la reunión de inicio («25 de noviembre») además del plazo de 25 días",
         "confusion": "tomar la fecha como cantidad de alguna actividad", "conceptos_confundibles": ["levan"]},
        {"id": "C1-T4", "valor_literal": "7", "valor": "7", "origen": {"documento": "solicitud_01.pdf", "pagina": 2},
         "es": "número de nota («Nota 7») además de 7 construcciones", "confusion": "tomarlo como cantidad de quebradas",
         "conceptos_confundibles": ["quebr", "drenaj"]},
    ],
    "elementos_visuales": [],
    "ocr": {"requiere": False, "motivo": "texto digital extraíble"},
    "inventario": {
        "paginas": [
            {"n": 1, "elementos": ["encabezado", "objeto", "datos del predio", "Tabla 1 (5 filas)"]},
            {"n": 2, "elementos": ["condiciones (6 puntos)", "notas", "contacto"]},
        ],
        "tablas": [{"id": 1, "pagina": 1, "filas": 5, "columnas": ["Ítem", "Actividad", "Cantidad", "Unidad", "Entregable"]}],
    },
}

# ------------------------------------------------------------------------------------------ C2: PDF escaneado
C2: Dato = {
    "id": CASO_2,
    "titulo": "Términos de referencia de levantamiento batimétrico",
    "parametros": {
        "entidad": "Empresa de Servicios Ejemplo E.S.P. (ficticia)", "numero": "0318-2026", "folio": "14",
        "embalse": "Embalse El Roble (ficticio)", "espejo": "45,0", "excluida": "3,2", "tachado": "45,0",
        "manuscrito": "41,8", "ronda": "25 m", "rampa": "6,8", "calado": "0,6", "copia": "0457",
    },
    "actividades": [
        _actividad("C2-A1", "Batimetría monohaz del espejo de agua", ("batim", "espej"), "45,0", "45.0", "ha",
                   "batimetria", {"documento": "tdr_02.pdf", "pagina": 1, "tabla": "Cuadro 1", "fila": "A"},
                   "Modelo digital del fondo y plano batimétrico", ("modelo", "plano"),
                   requiere_ocr=True, requiere_vision=True,
                   nota_visual="la cifra impresa está tachada y hay una anotación manuscrita de 41,8 ha"),
        _actividad("C2-A2", "Perfiles longitudinales de fondo", ("perfil", "longitud"), "3", "3", "perfiles",
                   "batimetria", {"documento": "tdr_02.pdf", "pagina": 1, "tabla": "Cuadro 1", "fila": "B"},
                   "Perfiles en PDF", ("perfil",), requiere_ocr=True),
        _actividad("C2-A3", "Secciones transversales (espaciamiento)", ("seccion", "transvers"), "50", "50", "m",
                   "batimetria", {"documento": "tdr_02.pdf", "pagina": 1, "tabla": "Cuadro 1", "fila": "C"},
                   "Secciones en PDF", ("seccion",), requiere_ocr=True),
        _actividad("C2-A4", "Topografía de la franja de ronda (25 m a cada lado del eje)", ("ronda", "topograf"),
                   "6,8", "6.8", "km", "topografia",
                   {"documento": "tdr_02.pdf", "pagina": 1, "tabla": "Cuadro 1", "fila": "D"},
                   "Plano de franja en DWG", ("dwg",), requiere_ocr=True),
    ],
    "restricciones": [
        {"id": "C2-R1", "texto": "El permiso de navegación está a cargo del contratista.", "claves": ["permiso", "navegacion"],
         "critica": False, "origen": {"documento": "tdr_02.pdf", "pagina": 2}},
        {"id": "C2-R2", "texto": "Franja de ronda de 25 m a cada lado del eje del embalse.", "claves": ["25", "ronda"],
         "critica": False, "origen": {"documento": "tdr_02.pdf", "pagina": 1}},
        {"id": "C2-R3", "texto": "Calado máximo de la embarcación: 0,6 m.", "claves": ["calado"], "critica": False,
         "origen": {"documento": "tdr_02.pdf", "pagina": 2}},
    ],
    "contradicciones": [
        {"id": "C2-K1", "tema": "área del espejo de agua a levantar", "critica": True,
         "valores": [{"literal": "45,0", "unidad": "ha", "origen": {"documento": "tdr_02.pdf", "pagina": 1,
                                                                    "tabla": "Cuadro 1", "fila": "A",
                                                                    "nota": "impresa y tachada"}},
                     {"literal": "41,8", "unidad": "ha", "origen": {"documento": "tdr_02.pdf", "pagina": 1,
                                                                    "nota": "manuscrita junto a la fila A"}}],
         "literales_clave": ["45,0", "41,8"], "palabras": ["espejo", "batim"],
         "motivo": "La cifra impresa está tachada y una anotación a mano da otra; el croquis excluye 3,2 ha."},
    ],
    "faltantes": [
        {"id": "C2-F1", "tema": "ubicacion", "bloqueante": True, "destino": "cliente",
         "palabras": ["ubicacion", "anexo", "plano", "municipio"],
         "motivo": "La ubicación se remite al «Anexo 3», que no viene; solo se nombra el embalse."},
        {"id": "C2-F2", "tema": "plazo", "bloqueante": False, "destino": "cliente", "palabras": ["plazo"],
         "motivo": "No se indica plazo de ejecución."},
        {"id": "C2-F3", "tema": "area final", "bloqueante": False, "destino": "cliente",
         "palabras": ["45,0", "41,8", "area"], "motivo": "Confirmar el área final (impresa, manuscrita y croquis)."},
    ],
    "revisiones_internas": [
        {"id": "C2-I1", "texto": "Evaluar si la profundidad máxima esperada de 14 m exige ecosonda de doble frecuencia.",
         "palabras": ["ecosonda", "frecuencia"]},
        {"id": "C2-I2", "texto": "Definir el nivel de referencia vertical del embalse antes de planear los perfiles.",
         "palabras": ["nivel de referencia", "vertical"]},
    ],
    "datos_presentes": [{"tema": "calado", "palabras": ["calado"]}, {"tema": "permiso", "palabras": ["permiso"]}],
    "trampas": [
        {"id": "C2-T1", "valor_literal": "6,8", "valor": "6.8", "origen": {"documento": "tdr_02.pdf", "pagina": 2},
         "es": "ancho de la rampa de acceso: 6,8 m", "confusion": "mezclar los 6,8 m de ancho con los 6,8 km de ronda",
         "conceptos_confundibles": ["rampa", "acceso"], "unidad_distinta": "m"},
        {"id": "C2-T2", "valor_literal": "3,2", "valor": "3.2", "origen": {"documento": "tdr_02.pdf", "pagina": 2,
                                                                          "elemento": "Figura 1 y Cuadro 2"},
         "es": "zona de captación excluida del levantamiento", "confusion": "sumarla o tomarla como área a levantar",
         "conceptos_confundibles": ["batim", "espej"]},
        {"id": "C2-T3", "valor_literal": "0457", "valor": "457", "origen": {"documento": "tdr_02.pdf", "pagina": 1},
         "es": "número del sello «copia controlada»", "confusion": "tomarlo como cantidad", "conceptos_confundibles": ["levan"]},
        {"id": "C2-T4", "valor_literal": "14", "valor": "14", "origen": {"documento": "tdr_02.pdf", "pagina": 2},
         "es": "profundidad máxima esperada: 14 m", "confusion": "tomarla como número de perfiles o secciones",
         "conceptos_confundibles": ["perfil", "seccion"]},
    ],
    "elementos_visuales": [
        {"id": "C2-V1", "pagina": 1, "descripcion": "cifra 45,0 tachada con una anotación manuscrita de 41,8",
         "interpretacion": "el área impresa fue corregida a mano; hay que preguntar cuál rige",
         "claves": ["tachad", "manuscrit", "41,8"]},
        {"id": "C2-V2", "pagina": 2, "descripcion": "Figura 1: zona rayada rotulada como excluida del levantamiento (3,2 ha)",
         "interpretacion": "la zona rayada no se levanta; el área neta es 45,0 − 3,2 = 41,8 ha (derivada, no impresa)",
         "claves": ["rayad", "excluid", "captacion"]},
        {"id": "C2-V3", "pagina": 2, "descripcion": "flecha norte y barra de escala (0, 250, 500 m)",
         "interpretacion": "orientación y escala gráfica; sin cantidad contratable", "claves": ["norte", "escala"]},
    ],
    "ocr": {"requiere": True, "motivo": "ambas páginas son una imagen sin texto extraíble",
            "paginas_con_ocr": [1, 2]},
    "inventario": {
        "paginas": [
            {"n": 1, "elementos": ["encabezado y sello", "objeto", "Cuadro 1 (4 filas)", "tachadura y anotación a mano"]},
            {"n": 2, "elementos": ["Figura 1 (croquis)", "leyenda", "Cuadro 2 (2 filas)", "notas"]},
        ],
        "tablas": [{"id": "Cuadro 1", "pagina": 1, "filas": 4, "columnas": ["Código", "Actividad", "Magnitud", "Unidad"]},
                   {"id": "Cuadro 2", "pagina": 2, "filas": 2, "columnas": ["Concepto", "Área (ha)"]}],
    },
}

# ----------------------------------------------------------------------------------------------- C3: Excel
DETALLE_LOTES = (("L-01", "La Esperanza", 48250), ("L-02", "La Esperanza", 61800), ("L-03", "El Mirador", 37400),
                 ("L-04", "El Mirador", 55120), ("L-05", "La Esperanza", 72930), ("L-06", "El Mirador", 61500))
DETALLE_VIAS = (("V-01", 1240), ("V-02", 865), ("V-03", 1520))
SUPUESTOS = (("Puntos de control por lote", 4, "puntos"), ("Equidistancia de curvas", 0.5, "m"),
             ("Precisión horizontal", 0.02, "m"))
PREDIOS_EN_RESUMEN = 7          # contradicción intencional: el detalle lista 6 lotes
BORRADOR_ANTERIOR = (("R-01", "Levantamiento topográfico de lotes", 31.2, "ha"),
                     ("R-02", "Levantamiento de ejes viales", 3.1, "km"),
                     ("R-03", "Puntos de control", 20, "puntos"))

_ORIGEN_C3 = {"documento": "cantidades_03.xlsx"}
C3: Dato = {
    "id": CASO_3,
    "titulo": "Resumen de cantidades del proyecto vial ficticio",
    "parametros": {"proyecto": "Proyecto vial Ejemplo (ficticio)", "veredas": "La Esperanza y El Mirador"},
    "actividades": [
        _actividad("C3-A1", "Levantamiento topográfico de lotes", ("lotes", "topograf"), "33.7", "33.7", "ha",
                   "topografia", {**_ORIGEN_C3, "hoja": "Resumen", "celda": "C4"}, formula="=Detalle!D8",
                   unidades_de_origen="Detalle!D2:D7 en ha; Detalle!C2:C7 en m2 (no se convierte)"),
        _actividad("C3-A2", "Levantamiento de ejes viales", ("ejes", "viales"), "3.625", "3.625", "km", "topografia",
                   {**_ORIGEN_C3, "hoja": "Resumen", "celda": "C5"}, formula="=SUM(Detalle!G2:G4)/1000",
                   unidades_de_origen="Detalle!G2:G4 en m"),
        _actividad("C3-A3", "Puntos de control", ("puntos", "control"), "24", "24", "puntos", "geodesia",
                   {**_ORIGEN_C3, "hoja": "Resumen", "celda": "C6"}, formula="=Supuestos!B2*COUNTA(Detalle!A2:A7)",
                   unidades_de_origen="4 puntos por lote × 6 lotes"),
        _actividad("C3-A4", "Perfiles de eje", ("perfiles", "eje"), "3", "3", "perfiles", "topografia",
                   {**_ORIGEN_C3, "hoja": "Resumen", "celda": "C7"}, formula="=COUNTA(Detalle!F2:F4)",
                   unidades_de_origen="un perfil por tramo"),
    ],
    "restricciones": [
        {"id": "C3-R1", "texto": "Equidistancia de curvas de nivel: 0,5 m.", "claves": ["0,5", "curvas"], "critica": False,
         "origen": {**_ORIGEN_C3, "hoja": "Supuestos", "celda": "B3"}},
        {"id": "C3-R2", "texto": "Precisión horizontal: 0,02 m.", "claves": ["0,02", "precision"], "critica": False,
         "origen": {**_ORIGEN_C3, "hoja": "Supuestos", "celda": "B4"}},
    ],
    "contradicciones": [
        {"id": "C3-K1", "tema": "número de predios intervenidos", "critica": True,
         "valores": [{"literal": "7", "unidad": "lotes", "origen": {**_ORIGEN_C3, "hoja": "Resumen", "celda": "C9"}},
                     {"literal": "6", "unidad": "lotes", "origen": {**_ORIGEN_C3, "hoja": "Detalle", "celda": "A2:A7"}}],
         "literales_clave": ["7", "6"], "palabras": ["predio", "lote"],
         "motivo": "El resumen declara 7 predios y el detalle solo lista 6 lotes."},
        {"id": "C3-K2", "tema": "cantidades de la hoja oculta frente a la visible", "critica": False,
         "valores": [{"literal": "31.2", "unidad": "ha", "origen": {**_ORIGEN_C3, "hoja": "Borrador_anterior", "celda": "C2",
                                                                    "nota": "hoja oculta"}},
                     {"literal": "33.7", "unidad": "ha", "origen": {**_ORIGEN_C3, "hoja": "Resumen", "celda": "C4"}}],
         "literales_clave": ["31.2", "33.7"], "palabras": ["oculta", "borrador"],
         "motivo": "Una hoja oculta conserva cantidades anteriores; no deben tomarse como vigentes."},
    ],
    "faltantes": [],
    "faltantes_bloqueantes_esperados": 0,
    "revisiones_internas": [
        {"id": "C3-I1", "texto": "Confirmar que la hoja oculta Borrador_anterior no es la vigente.",
         "palabras": ["oculta", "borrador"]},
    ],
    "datos_presentes": [{"tema": "ubicacion", "palabras": ["vereda"]}],
    "trampas": [
        {"id": "C3-T1", "valor_literal": "337000", "valor": "337000", "origen": {**_ORIGEN_C3, "hoja": "Detalle", "celda": "C8"},
         "es": "suma de áreas en m2", "confusion": "tomarla como hectáreas o mezclar la unidad con ha",
         "conceptos_confundibles": ["lotes", "topograf"], "unidad_distinta": "m2"},
        {"id": "C3-T2", "valor_literal": "31.2", "valor": "31.2", "origen": {**_ORIGEN_C3, "hoja": "Borrador_anterior", "celda": "C2"},
         "es": "área de la hoja oculta", "confusion": "tomarla como vigente", "conceptos_confundibles": ["lotes", "topograf"]},
        {"id": "C3-T3", "valor_literal": "4", "valor": "4", "origen": {**_ORIGEN_C3, "hoja": "Supuestos", "celda": "B2"},
         "es": "puntos de control por lote (parámetro)", "confusion": "tomarlo como total de puntos de control",
         "conceptos_confundibles": ["puntos", "control"]},
    ],
    "elementos_visuales": [],
    "ocr": {"requiere": False, "motivo": "celdas con valor y fórmula; no hay imágenes"},
    "inventario": {
        "hojas": [
            {"nombre": "Resumen", "visibilidad": "visible", "rango": "A1:E9", "formulas": ["C4", "C5", "C6", "C7"],
             "comentarios": ["C9"]},
            {"nombre": "Detalle", "visibilidad": "visible", "rango": "A1:G8", "formulas": ["D2:D7", "C8", "D8"]},
            {"nombre": "Supuestos", "visibilidad": "visible", "rango": "A1:C4", "formulas": []},
            {"nombre": "Borrador_anterior", "visibilidad": "hidden", "rango": "A1:D4", "formulas": []},
        ],
    },
}

CASOS: dict[str, Dato] = {CASO_1: C1, CASO_2: C2, CASO_3: C3}
