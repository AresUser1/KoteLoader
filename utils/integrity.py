# utils/integrity.py

import hashlib
import os
import sys
from pathlib import Path

# Файлы, изменение которых во время работы считается АТАКОЙ
CRITICAL_FILES = [
    "utils/security.py",
    "utils/loader.py",
    "utils/integrity.py",
    "modules/install.py",
    "modules/modules.py",
    "main.py"
]

_SNAPSHOT = {}
ROOT_DIR = Path(__file__).parent.parent

def calculate_file_hash(path: Path) -> str:
    """Считает SHA-256 хеш файла."""
    sha256 = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                sha256.update(data)
        return sha256.hexdigest()
    except FileNotFoundError:
        return "DELETED"
    except Exception:
        return "ERROR"

def initialize_snapshot():
    """Создает снимок состояния файлов при запуске бота."""
    global _SNAPSHOT
    _SNAPSHOT = {}
    for filename in CRITICAL_FILES:
        path = ROOT_DIR / filename
        if path.exists():
            _SNAPSHOT[filename] = calculate_file_hash(path)

def verify_integrity() -> dict:
    """
    Проверяет, не изменились ли критические файлы.
    Возвращает {'status': 'ok'} или {'status': 'compromised', 'details': ...}
    """
    if not _SNAPSHOT:
        initialize_snapshot()

    for filename, original_hash in _SNAPSHOT.items():
        path = ROOT_DIR / filename
        current_hash = calculate_file_hash(path)
        
        if current_hash != original_hash:
            return {
                "status": "compromised",
                "file": filename,
                "reason": "File modified during runtime" if current_hash != "DELETED" else "File deleted"
            }
            
    return {"status": "ok"}
