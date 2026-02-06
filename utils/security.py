# utils/security.py

import ast
from telethon.tl.functions.account import DeleteAccountRequest
from telethon.tl.functions.auth import ResetAuthorizationsRequest

# --- ПРАВИЛА БЕЗОПАСНОСТИ ---
BLOCK_LIST = {
    # Критически опасные функции
    "functions": {
        "os.system", "os.popen", "os.exec", "os.spawn",
        "subprocess.run", "subprocess.call", "subprocess.Popen",
        "exec", "eval", "__import__",
        "DeleteAccountRequest", "ResetAuthorizationsRequest" # Блокируем импорт или вызов этих классов
    },
    # Опасные строки
    "strings": {
        ".session", "config.ini", "my_account.session"
    },
    # Критические модули
    "imports": {
        "telethon.tl.functions.account", # Целиком блокируем модуль удаления
        "telethon.tl.functions.auth",    # Целиком блокируем модуль авторизации (сброс сессий)
    }
}

WARN_LIST = {
    "imports": {"shutil", "ftplib", "smtplib"},
    "functions": {"open", "getattr", "setattr"},
}

INFO_LIST = {
    "imports": {"requests", "aiohttp", "socket", "urllib"},
}

class CodeVisitor(ast.NodeVisitor):
    """Обходит дерево кода и ищет опасные узлы."""
    def __init__(self):
        self.threats = set()
        self.level = "safe"

    def _update_level(self, new_level: str):
        """Обновляет уровень угрозы с учетом приоритета."""
        levels = {"safe": 0, "info": 1, "warning": 2, "block": 3}
        if levels[new_level] > levels[self.level]:
            self.level = new_level

    def visit_Call(self, node):
        """Анализирует вызовы функций."""
        func_name = ""
        # Обработка вызовов вида module.func()
        if isinstance(node.func, ast.Attribute):
            if hasattr(node.func, 'value') and isinstance(node.func.value, ast.Name):
                func_name = f"{node.func.value.id}.{node.func.attr}"
        # Обработка вызовов вида func()
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id

        # Проверка по списку
        if func_name in BLOCK_LIST["functions"]:
            self.threats.add(f"КРИТИЧЕСКОЕ: Вызов запрещенной функции `{func_name}`")
            self._update_level("block")
        
        # Специфичная проверка для subprocess (ловит subprocess.call и т.д.)
        if "subprocess" in func_name:
             self.threats.add(f"КРИТИЧЕСКОЕ: Запуск системных процессов `{func_name}`")
             self._update_level("block")

        if func_name in WARN_LIST["functions"]:
            self.threats.add(f"Подозрительно: Функция `{func_name}`")
            self._update_level("warning")
        
        self.generic_visit(node)

    def visit_Import(self, node):
        """Анализирует импорты (import os)."""
        for alias in node.names:
            if alias.name in BLOCK_LIST.get("imports", {}):
                self.threats.add(f"КРИТИЧЕСКОЕ: Импорт запрещенного модуля `{alias.name}`")
                self._update_level("block")
            elif alias.name in WARN_LIST.get("imports", {}):
                self.threats.add(f"Подозрительно: Импорт `{alias.name}`")
                self._update_level("warning")
            elif alias.name in INFO_LIST.get("imports", {}):
                self.threats.add(f"Сеть: Модуль использует `{alias.name}`")
                self._update_level("info")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        """Анализирует импорты из (from os import system)."""
        if node.module:
            if node.module in BLOCK_LIST.get("imports", {}):
                self.threats.add(f"КРИТИЧЕСКОЕ: Импорт из запрещенного модуля `{node.module}`")
                self._update_level("block")
            elif node.module in WARN_LIST.get("imports", {}):
                self.threats.add(f"Подозрительно: Импорт из `{node.module}`")
                self._update_level("warning")
            elif node.module in INFO_LIST.get("imports", {}):
                self.threats.add(f"Сеть: Модуль использует `{node.module}`")
                self._update_level("info")
        
        # Проверяем конкретные имена импортируемых функций
        for alias in node.names:
            if alias.name in BLOCK_LIST["functions"]:
                 self.threats.add(f"КРИТИЧЕСКОЕ: Импорт опасной функции `{alias.name}`")
                 self._update_level("block")

        self.generic_visit(node)

    def visit_Constant(self, node):
        """Анализирует строковые константы."""
        if isinstance(node.value, str):
            for blocked_str in BLOCK_LIST["strings"]:
                if blocked_str in node.value:
                    self.threats.add(f"КРИТИЧЕСКОЕ: Попытка доступа к `{blocked_str}`")
                    self._update_level("block")
        self.generic_visit(node)

def scan_code(code_content: str) -> dict:
    """Сканирует код и возвращает 'чистый' результат сканирования."""
    try:
        tree = ast.parse(code_content)
        visitor = CodeVisitor()
        visitor.visit(tree)
        
        if not visitor.threats:
            return {"level": "safe", "reasons": ["Опасных конструкций не найдено."]}
        
        return {"level": visitor.level, "reasons": sorted(list(visitor.threats))}
        
    except SyntaxError as e:
        # Синтаксическая ошибка не опасна сама по себе, но модуль не загрузится
        return {"level": "safe", "reasons": [f"Синтаксическая ошибка (не опасно): {e}"]}
    except Exception as e:
        return {"level": "block", "reasons": [f"Ошибка анализатора: {e}"]}

def check_permission(event, min_level: str = "TRUSTED") -> bool:
    """
    Проверяет права пользователя.
    min_level: "OWNER" или "TRUSTED"
    """
    from utils import database as db
    
    user_level = db.get_user_level(event.sender_id)
    
    if min_level == "OWNER" and user_level != "OWNER":
        return False
    if min_level == "TRUSTED" and user_level not in ["OWNER", "TRUSTED"]:
        return False
    
    return True

from telethon import TelegramClient
import typing

class CustomTelegramClient(TelegramClient):
    """Кастомный клиент с кэшированием сущностей и защитой, как в Heroku."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Кэш для пользователей и чатов, чтобы не спамить запросами к серверу
        self._entity_cache = {}

    async def get_entity(self, entity):
        # Если сущность уже в кэше — отдаем сразу
        if isinstance(entity, (int, str)) and entity in self._entity_cache:
            return self._entity_cache[entity]
        
        try:
            res = await super().get_entity(entity)
            if res:
                # Сохраняем по ID и по Username (если есть)
                self._entity_cache[res.id] = res
                if hasattr(res, 'username') and res.username:
                    self._entity_cache[res.username] = res
            return res
        except Exception as e:
            raise e

    async def __call__(self, request, *args, **kwargs):
        # Блокировка опасных запросов (Runtime Protection)
        blocked_requests = (
            DeleteAccountRequest, 
            ResetAuthorizationsRequest,
            "GetAuthorizationsRequest",
            "UpdatePasswordSettingsRequest"
        )
        
        req_name = request.__class__.__name__
        if isinstance(request, blocked_requests) or req_name in blocked_requests:
            raise SecurityError(f"🚫 Безопасность: Запрос {req_name} заблокирован!")
        
        return await super().__call__(request, *args, **kwargs)

class SecurityError(Exception):
    pass

def get_safe_client(client_class=CustomTelegramClient):
    """Возвращает класс кастомного клиента."""
    return client_class