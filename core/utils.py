import os
import sys
from pathlib import Path
import portalocker

def get_file_lock(file_path: str):
    """
    Retorna un objeto de bloqueo para un archivo que funciona entre procesos (multi-script).
    Requiere que el archivo lock exista o se pueda crear.
    """
    lock_path = str(file_path) + ".lock"
    return portalocker.Lock(lock_path, timeout=5)
