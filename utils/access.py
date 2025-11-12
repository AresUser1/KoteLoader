import json
from pathlib import Path

ACCESS_FILE = Path(__file__).parent.parent / "access.json"
OWNER_ID = 0  # Будет определен при первом запуске

def setup_owner(owner_id: int):
    """Определяет владельца при первом запуске."""
    global OWNER_ID
    OWNER_ID = owner_id
    if not ACCESS_FILE.exists():
        with ACCESS_FILE.open("w", encoding="utf-8") as f:
            json.dump({"owner_id": owner_id, "trusted_users": [owner_id]}, f, indent=4)

def is_authorized(user_id: int) -> bool:
    """Проверяет, есть ли у пользователя доступ."""
    if not ACCESS_FILE.exists():
        return user_id == OWNER_ID  # На случай, если файл удален, только владелец имеет доступ

    with ACCESS_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    
    return user_id in data.get("trusted_users", [])