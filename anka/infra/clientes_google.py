"""La forma mínima de los clientes de Gmail y de Drive que usa Anka (`Protocol`), para tiparlos sin `Any` (U-65).

`googleapiclient` construye sus métodos en ejecución, así que no se puede tipar de verdad; estos protocolos dicen lo
único que el código de Anka da por hecho —de dónde cuelgan las llamadas— y dejan a los dobles de prueba cumplirlos
sin heredar de nada. Los resultados de cada llamada siguen siendo `Any`: son JSON de Google.
"""

from __future__ import annotations

from typing import Any, Protocol


class ClienteGmail(Protocol):
    """Gmail: `gmail.users().messages()/threads()/drafts()/...`."""

    def users(self) -> Any:
        """El recurso `users` de la API de Gmail (de ahí cuelgan mensajes, hilos y borradores)."""
        ...


class ClienteDrive(Protocol):
    """Drive: `drive.files()/permissions()/...`."""

    def files(self) -> Any:
        """El recurso `files` de la API de Drive."""
        ...
