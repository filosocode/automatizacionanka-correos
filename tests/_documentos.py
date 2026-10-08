"""Documentos reales mínimos para probar los lectores: se construyen en memoria, sin archivos de ejemplo."""

import io
import zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
ODF_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
ODF_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
ODF_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
ODF_DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
PNG_1X1 = (  # una imagen PNG válida de 1x1 píxel
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00"
    b"\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


def zip_de(archivos: dict[str, bytes | str]) -> bytes:
    """ZIP en memoria con los archivos dados (nombre -> contenido)."""
    flujo = io.BytesIO()
    with zipfile.ZipFile(flujo, "w", zipfile.ZIP_DEFLATED) as zip_:
        for nombre, contenido in archivos.items():
            zip_.writestr(nombre, contenido)
    return flujo.getvalue()


def parrafo(texto: str) -> str:
    return f"<w:p><w:r><w:t>{texto}</w:t></w:r></w:p>"


def docx(cuerpo: str, extra: dict[str, bytes | str] | None = None) -> bytes:
    """Documento Word con el cuerpo WordprocessingML dado y las partes adicionales (encabezados, notas, imágenes)."""
    documento = f'<w:document xmlns:w="{W}"><w:body>{cuerpo}</w:body></w:document>'
    return zip_de({"word/document.xml": documento, **(extra or {})})


def tabla_docx(filas: list[list[str]]) -> str:
    celdas = "".join("<w:tr>" + "".join(f"<w:tc>{parrafo(c)}</w:tc>" for c in fila) + "</w:tr>" for fila in filas)
    return f"<w:tbl>{celdas}</w:tbl>"


def pptx(diapositivas: list[str], extra: dict[str, bytes | str] | None = None) -> bytes:
    """Presentación con una diapositiva por cada texto (un párrafo cada una)."""
    partes: dict[str, bytes | str] = {}
    for numero, texto in enumerate(diapositivas, 1):
        partes[f"ppt/slides/slide{numero}.xml"] = (
            f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp><p:txBody>'
            f"<a:p><a:r><a:t>{texto}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )
    return zip_de({**partes, **(extra or {})})


def odf(contenido: str, extra: dict[str, bytes | str] | None = None) -> bytes:
    """Documento OpenDocument (odt/ods/odp) con el `content.xml` dado."""
    xml = (
        f'<office:document-content xmlns:office="{ODF_OFFICE}" xmlns:text="{ODF_TEXT}" '
        f'xmlns:table="{ODF_TABLE}" xmlns:draw="{ODF_DRAW}"><office:body>{contenido}</office:body>'
        "</office:document-content>"
    )
    return zip_de({"content.xml": xml, **(extra or {})})


def pdf_con_texto(paginas: list[str]) -> bytes:
    """PDF mínimo, escrito a mano, con el texto nativo de cada página (una línea por página)."""
    objetos: list[bytes] = []

    def agregar(contenido: bytes) -> int:
        objetos.append(contenido)
        return len(objetos)

    agregar(b"<< /Type /Catalog /Pages 2 0 R >>")
    hijos = " ".join(f"{3 + 2 * n} 0 R" for n in range(len(paginas)))
    agregar(f"<< /Type /Pages /Kids [{hijos}] /Count {len(paginas)} >>".encode())
    fuente = 3 + 2 * len(paginas)
    for n, texto in enumerate(paginas):
        agregar(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * n} 0 R "
            f"/Resources << /Font << /F1 {fuente} 0 R >> >> >>".encode()
        )
        flujo = f"BT /F1 12 Tf 50 700 Td ({texto}) Tj ET".encode("latin-1")
        agregar(b"<< /Length " + str(len(flujo)).encode() + b" >>\nstream\n" + flujo + b"\nendstream")
    agregar(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    salida = bytearray(b"%PDF-1.4\n")
    posiciones = []
    for numero, objeto in enumerate(objetos, 1):
        posiciones.append(len(salida))
        salida += f"{numero} 0 obj\n".encode() + objeto + b"\nendobj\n"
    inicio = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    for posicion in posiciones:
        salida += f"{posicion:010d} 00000 n \n".encode()
    salida += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{inicio}\n%%EOF\n".encode()
    return bytes(salida)
