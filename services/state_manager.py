import json
from pathlib import Path

STATE_FILE = Path(__file__).parent.parent / "state.json"

def get_loaded_modules() -> set:
    """Читает state.json и возвращает множество загруженных модулей."""
    if not STATE_FILE.exists():
        return set()
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, TypeError):
        return set()

def update_state_file(user_client):
    """Сохраняет текущий список загруженных модулей в state.json."""
    loaded = list(user_client.modules.keys())
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(loaded, f, indent=4)
    print(f"📝 Файл состояния обновлен: {len(loaded)} модулей загружено.")