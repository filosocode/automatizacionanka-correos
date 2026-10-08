r"""Se importa primero en cada módulo de tests: manda los datos a una carpeta temporal.

Si un test olvida parchear `settings.DATA_DIR`, escribe aquí y no en la carpeta real
(`%LOCALAPPDATA%\AnkaCorreos`) con credenciales y casos de verdad.
"""

import atexit
import os
import shutil
import tempfile

_dir = tempfile.mkdtemp(prefix="anka-tests-")
os.environ["ANKA_DATA_DIR"] = _dir
atexit.register(shutil.rmtree, _dir, ignore_errors=True)

# La actualización automática de la hoja de Google corre en segundo plano: en las pruebas no hace nada (las pruebas de
# esa función crean su propio sincronizador). Así ninguna prueba puede llegar a Google.
from anka.servicios import hoja_de_calculo  # noqa: E402  (después de fijar ANKA_DATA_DIR)

hoja_de_calculo.sincronizador = hoja_de_calculo.SincronizadorDeHojas(
    sincronizar=lambda hilo: hoja_de_calculo.ResultadoHoja("sin_caso", "", ""), espera=0, en_hilo=False)
