# utils/loader.py

import importlib
import sys
import re
import inspect
import traceback
from pathlib import Path
from telethon import events
from telethon.tl.custom import Button

MODULES_DIR = Path(__file__).parent.parent / "modules"
PREFIX = "."

# --- РЕЕСТРЫ ---
COMMANDS_REGISTRY = {}
CALLBACK_REGISTRY = {}
INLINE_HANDLERS_REGISTRY = {}
WATCHERS_REGISTRY = [] 

# --- Базовый класс для модулей ---
class Module:
    def __init__(self):
        self.client = None
        self.db = None
        self.config = {}

    async def client_ready(self, client, db):
        pass

# --- Декораторы ---
def register(command: str, **kwargs):
    kwargs.setdefault("outgoing", True)
    def decorator(func):
        async def wrapper(event, *args, **kwargs):
            from utils import database as db
            is_enabled = db.get_setting("userbot_enabled", default="True") == "True"
            command_name = command
            if not is_enabled:
                if db.get_user_level(event.sender_id) == "OWNER" and command_name == "on": pass
                else: return
            await func(event, *args, **kwargs)
        
        wrapper._is_command = True
        wrapper._command_name = command
        wrapper._command_kwargs = kwargs
        wrapper._command_doc = func.__doc__
        return wrapper
    return decorator

def watcher(**kwargs):
    def decorator(func):
        func._is_watcher = True
        func._watcher_kwargs = kwargs
        return func
    return decorator

def callback_handler(data_pattern: str):
    def decorator(func):
        func._is_callback_handler = True
        func._callback_pattern = re.compile(data_pattern)
        return func
    return decorator

def inline_handler(query_pattern: str, title: str, description: str = ""):
    def decorator(func):
        func._is_inline_handler = True
        func._inline_query_pattern = re.compile(query_pattern)
        func._inline_title = title
        func._inline_description = description
        return func
    return decorator

def check_module_dependencies(module_name: str) -> dict:
    """
    Проверяет зависимости модуля без полной загрузки.
    Возвращает dict со статусом.
    """
    try:
        # ❗️❗️❗️ ИЗМЕНЕНИЕ: .lower() УБРАН ❗️❗️❗️
        importlib.import_module(f"modules.{module_name}")
        return {"status": "ok"}
    except (ImportError, ModuleNotFoundError) as e:
        missing_lib_match = re.search(r"No module named '(\w+)'", str(e))
        if missing_lib_match:
            return {"status": "error", "library": missing_lib_match.group(1)}
        else:
            return {"status": "error", "library": "unknown", "details": str(e)}
    except Exception as e:
        return {"status": "error", "library": "unknown", "details": str(e)}

# --- Основная логика загрузчика ---

async def load_module(client, module_name: str, chat_id: int = None) -> dict:
    """Загружает модуль и регистрирует его обработчики."""
    if module_name in client.modules:
        return {"status": "info", "message": f"Модуль {module_name} уже загружен."}

    try:
        # ❗️❗️❗️ ИЗМЕНЕНИЕ: УБРАНО .lower() ❗️❗️❗️
        # module_name (например, 'Spammer.spam') УЖЕ имеет правильный регистр,
        # так как он приходит из _find_module_by_name() в modules.py
        import_name = f"modules.{module_name}"
        
        if import_name in sys.modules:
            importlib.reload(sys.modules[import_name])
        imported_module = importlib.import_module(import_name)

        registered_handlers = []
        module_instance = None
        for name, obj in inspect.getmembers(imported_module, inspect.isclass):
            if issubclass(obj, Module) and obj is not Module:
                module_instance = obj()
                module_instance.client = client
                from utils import database
                module_instance.db = database
                if hasattr(module_instance, "client_ready"):
                    await module_instance.client_ready(client, database)
                break
        search_target = module_instance if module_instance else imported_module
        
        for name, func in inspect.getmembers(search_target):
            if not (inspect.isfunction(func) or inspect.ismethod(func)):
                continue

            if getattr(func, "_is_command", False):
                command_name, handler_args, doc = func._command_name, func._command_kwargs, func._command_doc
                pattern_text = re.escape(PREFIX) + command_name + r"(?:\s+(.*))?$"
                # ❗️❗️❗️ ИСПРАВЛЕНИЕ: Добавлен re.DOTALL, чтобы (.*) захватывал новые строки ❗️❗️❗️
                handler_args["pattern"] = re.compile(pattern_text, re.IGNORECASE | re.DOTALL)
                handler = events.NewMessage(**handler_args)
                client.add_event_handler(func, handler)
                registered_handlers.append((func, handler))
                if command_name not in COMMANDS_REGISTRY: COMMANDS_REGISTRY[command_name] = []
                COMMANDS_REGISTRY[command_name].append({"module": module_name, "doc": doc or "Нет описания"})

            if getattr(func, "_is_watcher", False):
                handler_args = func._watcher_kwargs.copy()
                handler = handler_args.get('event') or events.NewMessage(**handler_args)
                client.add_event_handler(func, handler)
                registered_handlers.append((func, handler))

            if getattr(func, "_is_callback_handler", False):
                CALLBACK_REGISTRY[func._callback_pattern] = func

            if getattr(func, "_is_inline_handler", False):
                INLINE_HANDLERS_REGISTRY[func._inline_query_pattern] = {
                    "func": func,
                    "title": func._inline_title,
                    "description": func._inline_description
                }
        
        client.modules[module_name] = {
            "module": imported_module,
            "instance": module_instance,
            "handlers": registered_handlers
        }
        
        return {"status": "ok", "message": f"Модуль {module_name} успешно загружен."}

    except (ImportError, ModuleNotFoundError) as e:
        traceback.print_exc()
        # Эта проверка все еще полезна, если пользователь введет имя с неверным регистром
        if "No module named" in str(e) and module_name.lower() != module_name:
             return {"status": "error", "message": f"Ошибка: {e}. Возможно, имя модуля должно быть в нижнем регистре: `{module_name.lower()}`"}
        return {"status": "error", "message": f"Ошибка при загрузке {module_name}:\n{e}"}
    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": f"Ошибка при загрузке {module_name}:\n{e}"}

async def unload_module(client, module_name: str) -> dict:
    """Выгружает модуль из памяти."""
    if module_name not in client.modules:
        return {"status": "info", "message": f"Модуль {module_name} не загружен."}

    try:
        module_data = client.modules[module_name]

        for func, handler in module_data["handlers"]:
            client.remove_event_handler(func, handler)

        for command in list(COMMANDS_REGISTRY):
            COMMANDS_REGISTRY[command] = [cmd for cmd in COMMANDS_REGISTRY[command] if cmd["module"] != module_name]
            if not COMMANDS_REGISTRY[command]: del COMMANDS_REGISTRY[command]

        # ❗️❗️❗️ ИЗМЕНЕНИЕ: УБРАНО .lower() ❗️❗️❗️
        for pattern in list(CALLBACK_REGISTRY):
            if CALLBACK_REGISTRY[pattern].__module__ == f"modules.{module_name}":
                del CALLBACK_REGISTRY[pattern]
        
        # ❗️❗️❗️ ИЗМЕНЕНИЕ: УБРАНО .lower() ❗️❗️❗️
        for pattern in list(INLINE_HANDLERS_REGISTRY):
            if INLINE_HANDLERS_REGISTRY[pattern]["func"].__module__ == f"modules.{module_name}":
                del INLINE_HANDLERS_REGISTRY[pattern]

        del client.modules[module_name]

        # ❗️❗️❗️ ИЗМЕНЕНИЕ: УБРАНО .lower() ❗️❗️❗️
        for name in list(sys.modules):
            if name == f"modules.{module_name}" or name.startswith(f"modules.{module_name}."):
                del sys.modules[name]

        return {"status": "ok", "message": f"Модуль {module_name} успешно выгружен."}
    except Exception as e:
        return {"status": "error", "message": f"Ошибка при выгрузке {module_name}:\n{e}"}

async def reload_module(client, module_name: str, chat_id: int = None) -> dict:
    """Перезагружает модуль."""
    unload_result = await unload_module(client, module_name)
    if unload_result["status"] == "error":
        return unload_result
    
    return await load_module(client, module_name, chat_id)

def get_all_modules() -> list[str]:
    """Рекурсивно ищет все .py файлы в папке modules и ее подпапках."""
    all_modules = []
    for path in MODULES_DIR.rglob("*.py"):
        if path.name.startswith("_"):
            continue
        
        # 'modules/Spammer/spam.py' -> 'Spammer/spam'
        relative_path = path.relative_to(MODULES_DIR)
        # 'Spammer/spam' -> ('Spammer', 'spam') -> 'Spammer.spam'
        import_path = ".".join(relative_path.with_suffix("").parts)
        all_modules.append(import_path)
        
    return all_modules