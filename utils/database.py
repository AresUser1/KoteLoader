# utils/database.py
import sqlite3
import json
from pathlib import Path
from typing import Any, Dict, Optional

DB_FILE = Path(__file__).parent.parent / "database.db"
connection = None


def db_connect():
    """Устанавливает соединение с базой данных в режиме автокоммита."""
    global connection
    if connection is None:
        # КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: isolation_level=None включает автокоммит.
        connection = sqlite3.connect(DB_FILE, isolation_level=None)
        connection.row_factory = sqlite3.Row
    return connection


def init_hidden_modules_table():
    """Создает таблицу для скрытых модулей, если она не существует."""
    cursor = connection.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hidden_modules (
            module_name TEXT PRIMARY KEY
        )
    """)


def init_db():
    """Инициализирует базу данных, создает основные таблицы."""
    print("Инициализация базы данных...")
    db = db_connect()
    cursor = db.cursor()

    cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, level TEXT NOT NULL)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS module_storage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module_name TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            storage_value TEXT NOT NULL,
            storage_type TEXT DEFAULT 'data',
            user_id INTEGER DEFAULT 0,
            chat_id INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_module_storage_lookup 
        ON module_storage(module_name, storage_key, storage_type, user_id, chat_id)
    """)
    # Инициализируем новую таблицу для скрытых модулей
    init_hidden_modules_table()
    print("База данных готова.")


def get_setting(key: str, default: str = None) -> str:
    """Получает значение настройки по ключу."""
    cursor = connection.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    result = cursor.fetchone()
    return result['value'] if result else default


def set_setting(key: str, value: str):
    """Устанавливает значение настройки."""
    cursor = connection.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def add_user(user_id: int, level: str):
    """Добавляет или обновляет уровень доступа пользователя."""
    cursor = connection.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (user_id, level) VALUES (?, ?)", (user_id, level))


def remove_user(user_id: int):
    """Удаляет пользователя из таблицы доступа."""
    cursor = connection.cursor()
    cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))


def get_user_level(user_id: int) -> str:
    """Получает уровень доступа пользователя."""
    cursor = connection.cursor()
    cursor.execute("SELECT level FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    return result['level'] if result else "USER"


def get_users_by_level(level: str) -> list:
    """Получает список ID пользователей с указанным уровнем."""
    cursor = connection.cursor()
    cursor.execute("SELECT user_id FROM users WHERE level = ?", (level,))
    return [row['user_id'] for row in cursor.fetchall()]

def _store_module_data(module_name: str, key: str, value: Any, storage_type: str = 'data', user_id: int = 0,
                       chat_id: int = 0):
    """Внутренняя функция для сохранения данных модуля с корректным обновлением."""
    cursor = connection.cursor()
    value_str = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value

    # Сначала пытаемся обновить существующую запись
    cursor.execute("""
        UPDATE module_storage 
        SET storage_value = ?, updated_at = CURRENT_TIMESTAMP
        WHERE module_name = ? AND storage_key = ? AND storage_type = ? AND user_id = ? AND chat_id = ?
    """, (value_str, module_name, key, storage_type, user_id, chat_id))

    # Если ни одна строка не была обновлена (т.е. записи не было), создаём новую
    if cursor.rowcount == 0:
        cursor.execute("""
            INSERT INTO module_storage 
            (module_name, storage_key, storage_value, storage_type, user_id, chat_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (module_name, key, value_str, storage_type, user_id, chat_id))


def _get_module_data(module_name: str, key: str, storage_type: str = 'data', default: Any = None, user_id: int = 0,
                     chat_id: int = 0) -> Any:
    """Внутренняя функция для получения данных модуля."""
    cursor = connection.cursor()
    cursor.execute("""
        SELECT storage_value FROM module_storage 
        WHERE module_name = ? AND storage_key = ? AND storage_type = ? AND user_id = ? AND chat_id = ?
    """, (module_name, key, storage_type, user_id, chat_id))
    result = cursor.fetchone()
    if not result: return default
    try:
        return json.loads(result['storage_value'])
    except (json.JSONDecodeError, TypeError):
        return result['storage_value']


def set_module_config(module_name: str, config_key: str, config_value: Any, user_id: int = 0):
    """Устанавливает конфигурацию модуля."""
    _store_module_data(module_name, config_key, config_value, 'config', user_id, 0)


def get_module_config(module_name: str, config_key: str, default: Any = None, user_id: int = 0) -> Any:
    """Получает конфигурацию модуля."""
    return _get_module_data(module_name, config_key, 'config', default, user_id, 0)


def set_module_data(module_name: str, data_key: str, data_value: Any, user_id: int = 0, chat_id: int = 0):
    """Сохраняет данные модуля."""
    _store_module_data(module_name, data_key, data_value, 'data', user_id, chat_id)


def get_module_data(module_name: str, data_key: str, default: Any = None, user_id: int = 0, chat_id: int = 0) -> Any:
    """Получает данные модуля."""
    return _get_module_data(module_name, data_key, 'data', default, user_id, chat_id)


def get_all_module_configs(module_name: str, user_id: int = 0) -> Dict[str, Any]:
    """Получает все конфигурации модуля."""
    cursor = connection.cursor()
    cursor.execute("""
        SELECT storage_key, storage_value FROM module_storage 
        WHERE module_name = ? AND storage_type = 'config' AND user_id = ? AND chat_id = 0
    """, (module_name, user_id))
    configs = {}
    for row in cursor.fetchall():
        try:
            configs[row['storage_key']] = json.loads(row['storage_value'])
        except (json.JSONDecodeError, TypeError):
            configs[row['storage_key']] = row['storage_value']
    return configs


def get_all_module_data(module_name: str, user_id: int = 0, chat_id: int = 0) -> Dict[str, Any]:
    """Получает все данные модуля."""
    cursor = connection.cursor()
    cursor.execute("""
        SELECT storage_key, storage_value FROM module_storage 
        WHERE module_name = ? AND storage_type = 'data' AND user_id = ? AND chat_id = ?
    """, (module_name, user_id, chat_id))
    data = {}
    for row in cursor.fetchall():
        try:
            data[row['storage_key']] = json.loads(row['storage_value'])
        except (json.JSONDecodeError, TypeError):
            data[row['storage_key']] = row['storage_value']
    return data


def remove_module_config(module_name: str, config_key: str = None, user_id: int = 0):
    """Удаляет конфигурацию модуля."""
    cursor = connection.cursor()
    if config_key:
        cursor.execute(
            "DELETE FROM module_storage WHERE module_name = ? AND storage_key = ? AND storage_type = 'config' AND user_id = ?",
            (module_name, config_key, user_id))
    else:
        cursor.execute("DELETE FROM module_storage WHERE module_name = ? AND storage_type = 'config' AND user_id = ?",
                       (module_name, user_id))


def remove_module_data(module_name: str, data_key: str = None, user_id: int = 0, chat_id: int = 0):
    """Удаляет данные модуля."""
    cursor = connection.cursor()
    if data_key:
        cursor.execute(
            "DELETE FROM module_storage WHERE module_name = ? AND storage_key = ? AND storage_type = 'data' AND user_id = ? AND chat_id = ?",
            (module_name, data_key, user_id, chat_id))
    else:
        cursor.execute(
            "DELETE FROM module_storage WHERE module_name = ? AND storage_type = 'data' AND user_id = ? AND chat_id = ?",
            (module_name, user_id, chat_id))


def clear_module(module_name: str):
    """Полностью очищает все данные модуля (config + data)."""
    cursor = connection.cursor()
    cursor.execute("DELETE FROM module_storage WHERE module_name = ?", (module_name,))
    print(f"🗑️ Все данные модуля '{module_name}' удалены.")


def get_modules_stats() -> Dict[str, Dict]:
    """Возвращает статистику по всем модулям."""
    cursor = connection.cursor()
    cursor.execute("""
        SELECT module_name, storage_type, COUNT(*) as entries_count, MAX(updated_at) as last_updated
        FROM module_storage GROUP BY module_name, storage_type ORDER BY module_name
    """)
    stats = {}
    for row in cursor.fetchall():
        module = row['module_name']
        if module not in stats:
            stats[module] = {'configs': 0, 'data_entries': 0, 'last_activity': None}
        if row['storage_type'] == 'config':
            stats[module]['configs'] = row['entries_count']
        elif row['storage_type'] == 'data':
            stats[module]['data_entries'] = row['entries_count']
        if not stats[module]['last_activity'] or row['last_updated'] > stats[module]['last_activity']:
            stats[module]['last_activity'] = row['last_updated']
    return stats


def get_all_module_sources() -> Dict[str, str]:
    """Получает словарь со всеми модулями и их URL-источниками."""
    cursor = connection.cursor()
    cursor.execute(
        "SELECT module_name, storage_value FROM module_storage WHERE storage_type = 'config' AND storage_key = 'source_url'")
    sources = {}
    for row in cursor.fetchall():
        sources[row['module_name']] = row['storage_value']
    return sources


def hide_module(module_name: str):
    """Добавляет модуль в список скрытых."""
    cursor = connection.cursor()
    cursor.execute("INSERT OR IGNORE INTO hidden_modules (module_name) VALUES (?)", (module_name,))


def unhide_module(module_name: str):
    """Удаляет модуль из списка скрытых."""
    cursor = connection.cursor()
    cursor.execute("DELETE FROM hidden_modules WHERE module_name = ?", (module_name,))


def get_hidden_modules() -> list:
    """Возвращает список всех скрытых модулей."""
    cursor = connection.cursor()
    cursor.execute("SELECT module_name FROM hidden_modules")
    return [row['module_name'] for row in cursor.fetchall()]


def close_db():
    """Корректно закрывает соединение с базой данных."""
    global connection
    if connection is not None:
        connection.close()
        connection = None
        print("Соединение с базой данных закрыто.")