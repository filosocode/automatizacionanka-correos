r"""Se importa primero en cada módulo de tests: manda los datos a una carpeta temporal.

Si un test olvida parchear `settings.DATA_DIR`, escribe aquí y no en la carpeta real
(`%LOCALAPPDATA%\AnkaCorreos`) con credenciales y casos de verdad.
"""

import atexit
import os
import shutil
import tempfile

# Variables del entorno de quien corre las pruebas que cambian lo que se ve o lo que se decide (p. ej. con
# OPENAI_API_KEY el selector de motor dice «OpenAI» y sin ella «OpenAI (sin clave)»). Las pruebas que necesitan una
# de ellas la fijan con `mock.patch.dict(os.environ, ...)`, así pasan igual con o sin la variable en esta máquina.
VARIABLES_DEL_ENTORNO_QUE_AFECTAN = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANKA_HOJA_AUTOMATICA", "ANKA_LOG_LEVEL", "ANKA_NO_BROWSER", "CODEX_HOME",
)
for _variable in VARIABLES_DEL_ENTORNO_QUE_AFECTAN:
    os.environ.pop(_variable, None)

_dir = tempfile.mkdtemp(prefix="anka-tests-")
os.environ["ANKA_DATA_DIR"] = _dir
atexit.register(shutil.rmtree, _dir, ignore_errors=True)

# La actualización automática de la hoja de Google corre en segundo plano: en las pruebas no hace nada (las pruebas de
# esa función crean su propio sincronizador). Así ninguna prueba puede llegar a Google.
from anka.servicios import hoja_de_calculo  # noqa: E402  (después de fijar ANKA_DATA_DIR)

hoja_de_calculo.sincronizador = hoja_de_calculo.SincronizadorDeHojas(
    sincronizar=lambda hilo: hoja_de_calculo.ResultadoHoja("sin_caso", "", ""), espera=0, en_hilo=False)
