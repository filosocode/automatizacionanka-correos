"""Representaciones PNG de páginas, separadas de la interpretación del PDF."""
from collections.abc import Callable
from io import BytesIO
from math import sqrt

MAX_PAGINAS = 30
MAX_PIXELES = 8_000_000
MAX_BYTES = 40 * 1024 * 1024


def vistas_pdf(contenido: bytes, guardar: Callable[[int, bytes], str]) -> dict:
    """Entrega páginas a `guardar(numero, png)` y registra la cobertura real."""
    paginas = []
    total = None
    usados = 0
    motivo = ''
    try:
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(contenido) as pdf:
            total = len(pdf)
            for indice in range(min(total, MAX_PAGINAS)):
                pagina = pdf[indice]
                try:
                    ancho, alto = pagina.get_size()
                    escala = min(3, sqrt(MAX_PIXELES / (ancho * alto)))
                    bitmap = pagina.render(scale=escala)
                    try:
                        imagen = bitmap.to_pil()
                        with BytesIO() as salida:
                            imagen.save(salida, format='PNG')
                            png = salida.getvalue()
                        dimensiones = imagen.size
                    finally:
                        bitmap.close()
                finally:
                    pagina.close()
                if usados + len(png) > MAX_BYTES:
                    motivo = 'límite de tamaño de las vistas'
                    break
                numero = indice + 1
                paginas.append({'pagina': numero, 'archivo': guardar(numero, png),
                                'ancho_px': dimensiones[0], 'alto_px': dimensiones[1]})
                usados += len(png)
            if not motivo and total > MAX_PAGINAS:
                motivo = 'límite de páginas del paquete'
    except (ImportError, OSError, RuntimeError, ValueError, ZeroDivisionError) as exc:
        motivo = f'no se pudo preparar la vista ({type(exc).__name__})'
    return {'total_paginas': total, 'paginas': paginas, 'motivo': motivo}
