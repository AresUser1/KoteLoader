# utils/database.py
import sqlite3
import json
from pathlib import Path
from typing import Any, Dict, Optional

DB_FILE = Path(__file__).parent.parent / "database.db"
connection = None

# --- КЭШИ В ПАМЯТИ (Для скорости) ---
_settings_cache: Dict[str, str] = {}
_users_cache: Dict[int, str] = {}
_users_list_cache: Dict[str, list] = {}
_aliases_cache: list = []  # Кэш для списка всех алиасов

def db_connect():
    """Устанавливает соединение с базой данных."""
    global connection
    if connection is None:
        # check_same_thread=False нужен для работы с асинхронностью Telethon
        connection = sqlite3.connect(DB_FILE, isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
    return connection

def init_hidden_modules_table():
    cursor = connection.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS hidden_modules (module_name TEXT PRIMARY KEY)")

def init_aliases_table():
    cursor = connection.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aliases (
            alias TEXT PRIMARY KEY, 
            real_command TEXT, 
            module_name TEXT
        )
    """)

def _warmup_cache():
    """Загружает часто используемые данные в память при старте."""
    print("🔥 Прогрев кэша базы данных...")
    cursor = connection.cursor()
    
    # 1. Кэшируем настройки
    cursor.execute("SELECT key, value FROM settings")
    for row in cursor.fetchall():
        _settings_cache[row['key']] = row['value']

    # 2. Кэшируем пользователей
    cursor.execute("SELECT user_id, level FROM users")
    for row in cursor.fetchall():
        uid, lvl = row['user_id'], row['level']
        _users_cache[uid] = lvl
        if lvl not in _users_list_cache:
            _users_list_cache[lvl] = []
        _users_list_cache[lvl].append(uid)

    # 3. Кэшируем алиасы
    global _aliases_cache
    cursor.execute("SELECT * FROM aliases")
    _aliases_cache = [dict(row) for row in cursor.fetchall()]

def init_db():
    """Инициализирует базу данных, создает таблицы и заполняет кэш."""
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
    
    init_hidden_modules_table()
    init_aliases_table()
    
    _warmup_cache()
    
    print("✅ База данных готова и закэширована.")

# --- РАБОТА С НАСТРОЙКАМИ (через кэш) ---

def get_setting(key: str, default: str = None) -> str:
    return _settings_cache.get(key, default)

def set_setting(key: str, value: str):
    _settings_cache[key] = value
    cursor = connection.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

# --- РАБОТА С ПОЛЬЗОВАТЕЛЯМИ (через кэш) ---

def add_user(user_id: int, level: str):
    _users_cache[user_id] = level
    # Сбрасываем списки, так как состав изменился
    global _users_list_cache
    _users_list_cache = {} 
    
    cursor = connection.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (user_id, level) VALUES (?, ?)", (user_id, level))

def remove_user(user_id: int):
    if user_id in _users_cache:
        del _users_cache[user_id]
        
    global _users_list_cache
    _users_list_cache = {}
    
    cursor = connection.cursor()
    cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))

def get_user_level(user_id: int) -> str:
    return _users_cache.get(user_id, "USER")

def get_users_by_level(level: str) -> list:
    if level in _users_list_cache:
        return _users_list_cache[level]
    
    # Если в кэше пусто (например, после сброса), читаем из БД
    cursor = connection.cursor()
    cursor.execute("SELECT user_id FROM users WHERE level = ?", (level,))
    res = [row['user_id'] for row in cursor.fetchall()]
    _users_list_cache[level] = res
    return res

# --- РАБОТА С ДАННЫМИ МОДУЛЕЙ (Напрямую в БД, JSON сложно кэшировать) ---

def _store_module_data(module_name: str, key: str, value: Any, storage_type: str = 'data', user_id: int = 0,
                       chat_id: int = 0):
    cursor = connection.cursor()
    value_str = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value

    cursor.execute("""
        UPDATE module_storage 
        SET storage_value = ?, updated_at = CURRENT_TIMESTAMP
        WHERE module_name = ? AND storage_key = ? AND storage_type = ? AND user_id = ? AND chat_id = ?
    """, (value_str, module_name, key, storage_type, user_id, chat_id))

    if cursor.rowcount == 0:
        cursor.execute("""
            INSERT INTO module_storage 
            (module_name, storage_key, storage_value, storage_type, user_id, chat_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (module_name, key, value_str, storage_type, user_id, chat_id))

def _get_module_data(module_name: str, key: str, storage_type: str = 'data', default: Any = None, user_id: int = 0,
                     chat_id: int = 0) -> Any:
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
    _store_module_data(module_name, config_key, config_value, 'config', user_id, 0)

def get_module_config(module_name: str, config_key: str, default: Any = None, user_id: int = 0) -> Any:
    return _get_module_data(module_name, config_key, 'config', default, user_id, 0)

def set_module_data(module_name: str, data_key: str, data_value: Any, user_id: int = 0, chat_id: int = 0):
    _store_module_data(module_name, data_key, data_value, 'data', user_id, chat_id)

def get_module_data(module_name: str, data_key: str, default: Any = None, user_id: int = 0, chat_id: int = 0) -> Any:
    return _get_module_data(module_name, data_key, 'data', default, user_id, chat_id)

def get_all_module_configs(module_name: str, user_id: int = 0) -> Dict[str, Any]:
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
    cursor = connection.cursor()
    if config_key:
        cursor.execute(
            "DELETE FROM module_storage WHERE module_name = ? AND storage_key = ? AND storage_type = 'config' AND user_id = ?",
            (module_name, config_key, user_id))
    else:
        cursor.execute("DELETE FROM module_storage WHERE module_name = ? AND storage_type = 'config' AND user_id = ?",
                       (module_name, user_id))

def remove_module_data(module_name: str, data_key: str = None, user_id: int = 0, chat_id: int = 0):
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
    cursor = connection.cursor()
    cursor.execute("DELETE FROM module_storage WHERE module_name = ?", (module_name,))
    print(f"🗑️ Все данные модуля '{module_name}' удалены.")

def get_modules_stats() -> Dict[str, Dict]:
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
    cursor = connection.cursor()
    cursor.execute(
        "SELECT module_name, storage_value FROM module_storage WHERE storage_type = 'config' AND storage_key = 'source_url'")
    sources = {}
    for row in cursor.fetchall():
        sources[row['module_name']] = row['storage_value']
    return sources

def hide_module(module_name: str):
    cursor = connection.cursor()
    cursor.execute("INSERT OR IGNORE INTO hidden_modules (module_name) VALUES (?)", (module_name,))

def unhide_module(module_name: str):
    cursor = connection.cursor()
    cursor.execute("DELETE FROM hidden_modules WHERE module_name = ?", (module_name,))

def get_hidden_modules() -> list:
    cursor = connection.cursor()
    cursor.execute("SELECT module_name FROM hidden_modules")
    return [row['module_name'] for row in cursor.fetchall()]

# --- ФУНКЦИИ АЛИАСОВ (С кэшированием) ---

def _refresh_aliases_cache():
    global _aliases_cache
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM aliases")
    _aliases_cache = [dict(row) for row in cursor.fetchall()]

def add_alias(alias: str, real_command: str, module_name: str):
    cursor = connection.cursor()
    cursor.execute("INSERT OR REPLACE INTO aliases (alias, real_command, module_name) VALUES (?, ?, ?)", 
                   (alias, real_command, module_name))
    _refresh_aliases_cache()

def remove_alias(alias: str):
    cursor = connection.cursor()
    cursor.execute("DELETE FROM aliases WHERE alias = ?", (alias,))
    _refresh_aliases_cache()

def get_aliases_by_command(real_command: str) -> list:
    # Ищем в кэше
    return [item['alias'] for item in _aliases_cache if item['real_command'] == real_command]

def get_all_aliases() -> list:
    return _aliases_cache

def close_db():
    global connection
    if connection is not None:
        connection.close()
        connection = None
        print("Соединение с базой данных закрыто.")
