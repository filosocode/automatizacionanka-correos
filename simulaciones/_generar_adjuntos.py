r"""Genera adjuntos ficticios (KMZ/KML, PDF, JPG, DWG falso) para los correos simulados.

Requiere reportlab y pillow. Uso (reportlab instalado en otra ruta):
    set PYTHONPATH=<ruta con reportlab>
    python simulaciones\_generar_adjuntos.py
Todo es ficticio y lleva la marca 'DOCUMENTO SIMULADO PARA PRUEBAS'.
"""
import math
import random
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

RAIZ = Path(__file__).parent / "adjuntos"
MARCA = "DOCUMENTO SIMULADO PARA PRUEBAS - DATOS FICTICIOS"
ESTILOS = getSampleStyleSheet()


# ---------- KML / KMZ ----------
def poligono(lat, lon, hectareas, rot=0.0):
    lado = math.sqrt(hectareas * 10_000)
    dlat = lado / 111_320 / 2
    dlon = lado / (111_320 * math.cos(math.radians(lat))) / 2
    pts = [(-dlon, -dlat), (dlon, -dlat), (dlon, dlat * 0.8), (-dlon * 0.6, dlat), (-dlon, dlat * 0.4)]
    c, s = math.cos(rot), math.sin(rot)
    return [(lon + x * c - y * s, lat + x * s + y * c) for x, y in pts]


def kml(nombre, pts, descripcion=""):
    coords = " ".join(f"{x:.6f},{y:.6f},0" for x, y in pts + [pts[0]])
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{nombre}</name><Placemark><name>{nombre}</name><description>{descripcion}</description>"
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates>"
        "</LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>"
    )


def linea_kml(nombre, pts):
    coords = " ".join(f"{x:.6f},{y:.6f},0" for x, y in pts)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<Placemark><name>{nombre}</name><LineString><tessellate>1</tessellate>"
        f"<coordinates>{coords}</coordinates></LineString></Placemark></Document></kml>"
    )


def kmz(path, contenido):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", contenido)


def kml_plano(path, contenido):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contenido, encoding="utf-8")


# ---------- PDF ----------
def pie(c, doc):
    c.saveState()
    c.setFont("Helvetica", 7)
    c.drawCentredString(doc.pagesize[0] / 2, 1 * cm, MARCA)
    c.restoreState()


def pdf_texto(path, titulo, secciones, tablas=None, paginas_extra=0):
    """secciones: lista de (encabezado, texto). tablas: {indice_seccion: filas}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=titulo, leftMargin=2.2 * cm, rightMargin=2.2 * cm)
    h = [Paragraph(titulo, ESTILOS["Title"]), Spacer(1, 10)]
    for i, (enc, txt) in enumerate(secciones):
        h.append(Paragraph(enc, ESTILOS["Heading2"]))
        for parrafo in txt.split("\n\n"):
            h.append(Paragraph(parrafo, ESTILOS["BodyText"]))
        if tablas and i in tablas:
            t = Table(tablas[i], hAlign="LEFT")
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, "#888888"), ("BACKGROUND", (0, 0), (-1, 0), "#DDDDDD"),
                                   ("FONTSIZE", (0, 0), (-1, -1), 8)]))
            h += [Spacer(1, 6), t]
        h.append(Spacer(1, 8))
    for n in range(paginas_extra):
        h += [PageBreak(), Paragraph(f"Capítulo {n + 2}. Disposiciones complementarias", ESTILOS["Heading2"])]
        h.append(Paragraph(RELLENO * 4, ESTILOS["BodyText"]))
    doc.build(h, onFirstPage=pie, onLaterPages=pie)


RELLENO = ("El contratista deberá ejecutar las actividades conforme a lo establecido en el presente documento, "
           "cumpliendo las normas técnicas vigentes, los protocolos de seguridad del contratante y los requisitos "
           "ambientales aplicables. Todo entregable será sometido a revisión de la interventoría. ")


def pdf_plano(path, titulo, lat_lon, hectareas, ubicacion, escala="1:2000", extra=None):
    """Plano simple con polígono y rótulo con ubicación y área."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = landscape(A3)
    c = canvas.Canvas(str(path), pagesize=(w, h))
    c.setTitle(titulo)
    c.setLineWidth(2)
    c.rect(1.5 * cm, 1.5 * cm, w - 3 * cm, h - 3 * cm)
    pts = poligono(lat_lon[0], lat_lon[1], hectareas)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    esc = min((w - 14 * cm) / (max(xs) - min(xs)), (h - 8 * cm) / (max(ys) - min(ys)))
    trans = [(3 * cm + (x - min(xs)) * esc, 5 * cm + (y - min(ys)) * esc) for x, y in pts]
    c.setStrokeColorRGB(0.1, 0.3, 0.7)
    c.setFillColorRGB(0.85, 0.92, 1)
    p = c.beginPath()
    p.moveTo(*trans[0])
    for t in trans[1:]:
        p.lineTo(*t)
    p.close()
    c.drawPath(p, fill=1, stroke=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 9)
    for i, t in enumerate(trans):
        c.drawString(t[0] + 4, t[1] + 4, f"V{i + 1}")
    # rotulo
    c.setLineWidth(1)
    c.rect(w - 11 * cm, 1.5 * cm, 9.5 * cm, 7 * cm)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(w - 10.6 * cm, 7.6 * cm, titulo)
    c.setFont("Helvetica", 9)
    filas = [f"UBICACIÓN: {ubicacion}", f"ÁREA: {hectareas} ha", f"ESCALA: {escala}",
             f"COORD. REF.: {lat_lon[0]:.4f}, {lat_lon[1]:.4f}", "FECHA: 2026-09", "PLANO N°: 01/01"]
    filas += extra or []
    for i, f in enumerate(filas):
        c.drawString(w - 10.6 * cm, (6.8 - i * 0.6) * cm, f)
    c.setFont("Helvetica", 7)
    c.drawCentredString(w / 2, 0.8 * cm, MARCA)
    c.save()


def pdf_planta_subestacion(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = landscape(A3)
    c = canvas.Canvas(str(path), pagesize=(w, h))
    c.setTitle("Plano de planta subestación Yumbo 115 kV")
    c.rect(1.5 * cm, 1.5 * cm, w - 3 * cm, h - 3 * cm)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(3 * cm, h - 3 * cm, "SUBESTACIÓN YUMBO 115 kV - PLANTA GENERAL (FICTICIA)")
    for i in range(4):
        x = 4 * cm + i * 8 * cm
        c.rect(x, 12 * cm, 6 * cm, 10 * cm)
        c.setFont("Helvetica", 9)
        c.drawString(x + 0.3 * cm, 21 * cm, f"Bahía {i + 1}")
        c.circle(x + 3 * cm, 17 * cm, 1 * cm)
    c.rect(4 * cm, 4 * cm, 10 * cm, 5 * cm)
    c.drawString(4.3 * cm, 8.3 * cm, "Edificio de control")
    c.rect(18 * cm, 4 * cm, 12 * cm, 5 * cm, stroke=1, fill=0)
    c.drawString(18.3 * cm, 8.3 * cm, "Patio de transformadores")
    c.setFont("Helvetica", 7)
    c.drawCentredString(w / 2, 0.8 * cm, MARCA)
    c.save()


def fuente(tam):
    for nombre in ("C:/Windows/Fonts/arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            pass
    return ImageFont.load_default()


def pdf_escaneado(path, lineas):
    """PDF de solo imagen: borroso, con ruido y ligera rotación (sin texto seleccionable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("L", (1240, 1754), 235)
    d = ImageDraw.Draw(img)
    d.rectangle((60, 60, 1180, 1694), outline=40, width=3)
    y = 120
    for i, ln in enumerate(lineas):
        d.text((110, y), ln, fill=30, font=fuente(34 if i == 0 else 26))
        y += 70 if i == 0 else 48
    d.polygon([(300, 700), (900, 650), (1000, 1000), (650, 1250), (320, 1100)], outline=50, width=4)
    random.seed(7)
    px = img.load()
    for _ in range(60_000):
        px[random.randrange(img.width), random.randrange(img.height)] = random.randrange(120, 255)
    img = img.rotate(1.6, fillcolor=235).filter(ImageFilter.GaussianBlur(2.2))
    img.convert("RGB").save(path, "PDF", resolution=150)


def jpg_captura(path):
    """Imitación burda de una captura satelital con un lote dibujado a mano."""
    path.parent.mkdir(parents=True, exist_ok=True)
    random.seed(3)
    img = Image.new("RGB", (1000, 700), (92, 125, 70))
    d = ImageDraw.Draw(img)
    for _ in range(900):
        x, y, r = random.randrange(1000), random.randrange(700), random.randrange(6, 40)
        g = random.randrange(-25, 25)
        d.ellipse((x, y, x + r, y + r), fill=(92 + g, 125 + g, 70 + g // 2))
    d.line([(0, 520), (1000, 430)], fill=(190, 180, 160), width=14)
    d.line([(520, 300), (700, 280), (720, 420), (540, 440), (520, 300)], fill=(255, 0, 0), width=5)
    d.text((10, 10), "captura de pantalla (simulada)", fill=(255, 255, 255), font=fuente(20))
    img.save(path, "JPEG", quality=85)


def archivo_corrupto(path, firma):
    path.parent.mkdir(parents=True, exist_ok=True)
    random.seed(11)
    path.write_bytes(firma + bytes(random.randrange(256) for _ in range(2048)))


def main() -> None:
    # --- 01 solar LiDAR ---
    c = RAIZ / "01-solar-lidar"
    kmz(c / "predio_candelaria_82ha.kmz", kml("Predio Candelaria", poligono(3.4100, -76.3500, 82), "82 ha"))
    pdf_plano(c / "implantacion_preliminar_45MWp.pdf", "Implantación preliminar parque solar 45 MWp",
              (3.4100, -76.3500), 82, "Candelaria, Valle del Cauca", extra=["CAPACIDAD: 45 MWp"])
    pdf_texto(c / "TdR_levantamiento_LiDAR.pdf", "Términos de referencia - Levantamiento LiDAR parque solar", [
        ("1. Objeto", "Levantamiento topográfico con LiDAR aerotransportado y ortofoto del predio destinado al parque solar de 45 MWp, municipio de Candelaria, Valle del Cauca."),
        ("2. Alcance", "Nube de puntos clasificada, MDT/MDS con curvas de nivel cada 0,5 m, ortofoto con GSD de 3 cm. Sistema de referencia MAGNA-SIRGAS, Origen Cali."),
        ("3. Plazos", "Trabajo de campo en noviembre de 2026. Entrega final dentro de los 15 días hábiles siguientes."),
        ("4. Normativa aplicable", "Cumplimiento de la reglamentación de la Aerocivil para operación de aeronaves no tripuladas y de las normas de seguridad industrial del contratante. (Texto ilustrativo.)"),
    ])
    # --- 02 via ---
    c = RAIZ / "02-via-topografia"
    eje = [(-77.2800 + i * 0.0105, 1.2100 + math.sin(i / 2) * 0.004 + i * 0.002) for i in range(12)]
    kml_plano(c / "eje_variante_sur.kml", linea_kml("Eje Variante Sur K0+000 - K12+500", eje))
    pdf_plano(c / "diseno_geometrico_planta.pdf", "Diseño geométrico Variante Sur - planta K0-K12+500",
              (1.2100, -77.2800), 75, "Pasto, Nariño", "1:5000", ["FRANJA: 60 m"])
    pdf_texto(c / "pliego_condiciones.pdf", "Pliego de condiciones - Topografía Variante Sur", [
        ("1. Precisión", "Precisión vertical exigida de ±5 cm en puntos de control y modelo digital. Precisión horizontal de ±3 cm."),
        ("2. Entregables", "Planimetría y altimetría, secciones transversales cada 20 m, inventario de alcantarillas y box culverts, ortofoto del corredor."),
        ("3. Plazo", "Máximo 30 días calendario desde la orden de inicio."),
    ], {1: [["Entregable", "Formato", "Escala"], ["Planos planta-perfil", "DWG/PDF", "1:1000"],
            ["Secciones transversales", "DWG", "1:200"], ["Ortofoto", "GeoTIFF", "GSD 3 cm"]]})
    # --- 03 batimetria ---
    c = RAIZ / "03-batimetria"
    kmz(c / "vaso_embalse_310ha.kmz", kml("Embalse La Esperanza", poligono(6.2330, -75.1600, 310), "310 ha"))
    pdf_texto(c / "informe_batimetria_2019.pdf", "Informe batimetría embalse La Esperanza - 2019", [
        ("Resumen", "Batimetría con ecosonda monohaz, líneas cada 50 m. Volumen total 48,2 Mm3 a cota 1.890 msnm. Área espejo 310 ha."),
        ("Resultados", "Profundidad máxima 38 m. Sedimentación estimada 0,8 Mm3 desde 2009."),
    ], {1: [["Cota (msnm)", "Área (ha)", "Volumen (Mm3)"], ["1.890", "310", "48,2"], ["1.880", "255", "23,9"], ["1.870", "180", "8,1"]]})
    # --- 04 subestación ---
    c = RAIZ / "04-subestacion-fotogrametria"
    pdf_planta_subestacion(c / "planta_subestacion_yumbo.pdf")
    jpg_captura(c / "foto_referencia_1.jpg")
    # --- 11 plano sin texto ---
    pdf_plano(RAIZ / "11-con-adjunto-sin-texto" / "plano_lote.pdf", "Plano de lote - Zona rural",
              (4.6200, -74.0800), 6.4, "Vereda El Rosal, Cota, Cundinamarca")
    # --- 13 contradictorio ---
    c = RAIZ / "13-ambiguo-doc-contradictorio"
    pdf_plano(c / "plano_proyecto.pdf", "Plano general del proyecto", (4.1500, -73.6400), 18, "Villavicencio, Meta")
    kmz(c / "poligono_proyecto.kmz", kml("Poligono proyecto", poligono(5.3300, -72.4000, 50), "Yopal, Casanare"))
    # --- 14 TdR extenso ---
    pdf_texto(RAIZ / "14-ambiguo-tdr-generico" / "TdR_proceso_seleccion_abreviada.pdf",
              "Términos de referencia - Estudios y diseños para mejoramiento vial", [
        ("1. Objeto", "Contratar los estudios de topografía, geotecnia, hidrología e hidráulica, diseño geométrico, estructuras y pavimentos para el mejoramiento de la vía terciaria del municipio. (Texto ficticio.)"),
        ("2. Componentes", "Componente A: topografía y fotogrametría. Componente B: estudios geotécnicos. Componente C: hidrología y obras de drenaje. Componente D: diseño geométrico y de pavimentos."),
        ("3. Normativa", "Se aplicarán las normas y manuales de diseño de entidades de infraestructura vial vigentes (listado ilustrativo, no es reglamentación real)."),
    ], paginas_extra=12)
    # --- 15 foto sola ---
    jpg_captura(RAIZ / "15-ambiguo-foto-sola" / "captura_lote.jpg")
    # --- 19 escaneado ---
    pdf_escaneado(RAIZ / "19-pdf-escaneado" / "plano_predio_escaneado.pdf", [
        "PLANO PREDIO RURAL", "Municipio: Zipaquirá", "Área aprox.: 30 ha", "Escala 1:2000", "Levantó: (ilegible)"])
    # --- 20 corruptos ---
    c = RAIZ / "20-adjunto-corrupto-o-raro"
    archivo_corrupto(c / "planos_linea.pdf", b"%PDF-1.4\n%corrupto")
    archivo_corrupto(c / "perfil_linea_transmision.dwg", b"AC1027")
    # --- 21 / 23 / 27 kmz ---
    kmz(RAIZ / "21-respuesta-completa-a-aclaracion" / "lote_rionegro.kmz", kml("Lote Rionegro", poligono(6.1500, -75.3700, 3.2)))
    kmz(RAIZ / "23-cambio-de-alcance" / "predio_actualizado_95ha.kmz", kml("Predio ampliado", poligono(3.4100, -76.3500, 95)))
    kmz(RAIZ / "27-en-ingles" / "site.kmz", kml("Solar site", poligono(10.4600, -73.2500, 60)))
    print("Adjuntos generados en", RAIZ)


if __name__ == "__main__":
    main()
