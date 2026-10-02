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
