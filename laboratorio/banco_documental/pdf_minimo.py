"""Escritor mínimo de PDF, determinista: sin fechas, sin identificadores aleatorios y sin dependencias nuevas.

Sirve para el documento digital (texto con fuente estándar, líneas y rectángulos) y para el escaneado (una imagen JPEG
por página). Los mismos datos producen siempre los mismos bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ANCHO_A4 = 595.0
ALTO_A4 = 842.0


def _cadena(texto: str) -> bytes:
    """Un texto como cadena literal PDF en WinAnsi (cp1252), con escapes."""
    crudo = texto.encode("cp1252")
    return b"(" + crudo.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _numero(valor: float) -> bytes:
    return f"{valor:.2f}".rstrip("0").rstrip(".").encode("ascii") if valor != int(valor) else str(int(valor)).encode()


@dataclass
class Pagina:
    """Una página: dibujo con el origen arriba a la izquierda (como se lee) o, si es escaneo, su imagen."""

    operaciones: list[bytes] = field(default_factory=list)
    jpeg: bytes | None = None
    ancho_px: int = 0
    alto_px: int = 0

    def texto(self, x: float, y: float, contenido: str, tam: float = 9.0, negrita: bool = False) -> None:
        """Escribe `contenido` con la base en (x, y) medida desde arriba."""
        fuente = b"/F2" if negrita else b"/F1"
        self.operaciones.append(b"BT " + fuente + b" " + _numero(tam) + b" Tf " + _numero(x) + b" "
                                + _numero(ALTO_A4 - y) + b" Td " + _cadena(contenido) + b" Tj ET")

    def linea(self, x1: float, y1: float, x2: float, y2: float, ancho: float = 0.6) -> None:
        """Traza un segmento."""
        self.operaciones.append(_numero(ancho) + b" w " + _numero(x1) + b" " + _numero(ALTO_A4 - y1) + b" m "
                                + _numero(x2) + b" " + _numero(ALTO_A4 - y2) + b" l S")

    def rectangulo(self, x: float, y: float, ancho: float, alto: float, gris: float | None = None) -> None:
        """Dibuja un rectángulo con su esquina superior izquierda en (x, y); `gris` (0 a 1) lo rellena."""
        base = _numero(x) + b" " + _numero(ALTO_A4 - y - alto) + b" " + _numero(ancho) + b" " + _numero(alto)
        if gris is None:
            self.operaciones.append(b"0.6 w " + base + b" re S")
        else:
            self.operaciones.append(_numero(gris) + b" g " + base + b" re f 0 g")


class Pdf:
    """Un documento de páginas A4; `serializar` devuelve los bytes."""

    def __init__(self, titulo: str) -> None:
        self.titulo = titulo
        self.paginas: list[Pagina] = []

    def nueva_pagina(self) -> Pagina:
        """Agrega una página en blanco y la devuelve."""
        pagina = Pagina()
        self.paginas.append(pagina)
        return pagina

    def pagina_de_imagen(self, jpeg: bytes, ancho_px: int, alto_px: int) -> Pagina:
        """Agrega una página cuyo único contenido es una imagen JPEG en escala de grises (un escaneo)."""
        pagina = Pagina(jpeg=jpeg, ancho_px=ancho_px, alto_px=alto_px)
        self.paginas.append(pagina)
        return pagina

    def serializar(self) -> bytes:
        """Los bytes del PDF; sin fecha de creación ni identificador, para que la generación sea reproducible."""
        objetos: list[bytes] = []

        def agregar(cuerpo: bytes) -> int:
            objetos.append(cuerpo)
            return len(objetos)

        agregar(b"")                                              # 1: catálogo (se completa al final)
        agregar(b"")                                              # 2: árbol de páginas
        fuentes = (agregar(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"),
                   agregar(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"))
        info = agregar(b"<< /Title " + _cadena(self.titulo) + b" >>")
        hijos: list[int] = []
        for pagina in self.paginas:
            recursos = (b"<< /Font << /F1 " + str(fuentes[0]).encode() + b" 0 R /F2 " + str(fuentes[1]).encode()
                        + b" 0 R >>")
            if pagina.jpeg is not None:
                imagen = agregar(b"<< /Type /XObject /Subtype /Image /Width " + str(pagina.ancho_px).encode()
                                 + b" /Height " + str(pagina.alto_px).encode()
                                 + b" /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /DCTDecode /Length "
                                 + str(len(pagina.jpeg)).encode() + b" >>\nstream\n" + pagina.jpeg + b"\nendstream")
                recursos += b" /XObject << /Im0 " + str(imagen).encode() + b" 0 R >>"
                flujo = f"q {ANCHO_A4:g} 0 0 {ALTO_A4:g} 0 0 cm /Im0 Do Q".encode()
            else:
                flujo = b"\n".join(pagina.operaciones)
            recursos += b" >>"
            contenido = agregar(b"<< /Length " + str(len(flujo)).encode() + b" >>\nstream\n" + flujo + b"\nendstream")
            hijos.append(agregar(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources " + recursos
                                 + b" /Contents " + str(contenido).encode() + b" 0 R >>"))
        objetos[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objetos[1] = (b"<< /Type /Pages /Kids [" + b" ".join(str(h).encode() + b" 0 R" for h in hijos)
                      + b"] /Count " + str(len(hijos)).encode() + b" >>")
        salida = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        posiciones = []
        for numero, cuerpo in enumerate(objetos, 1):
            posiciones.append(len(salida))
            salida += str(numero).encode() + b" 0 obj\n" + cuerpo + b"\nendobj\n"
        inicio = len(salida)
        salida += b"xref\n0 " + str(len(objetos) + 1).encode() + b"\n0000000000 65535 f \n"
        for posicion in posiciones:
            salida += f"{posicion:010d} 00000 n \n".encode()
        salida += (b"trailer\n<< /Size " + str(len(objetos) + 1).encode() + b" /Root 1 0 R /Info "
                   + str(info).encode() + b" 0 R >>\nstartxref\n" + str(inicio).encode() + b"\n%%EOF\n")
        return bytes(salida)
