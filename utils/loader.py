# utils/loader.py

import importlib
import sys
import os
import re
import inspect
import traceback
from pathlib import Path
from telethon import events
from telethon.tl.custom import Button
# Импортируем типы для форматирования
from telethon.tl.types import (
    MessageEntityBold, 
    MessageEntityCode, 
    MessageEntityBlockquote,
    MessageEntityPre
)

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
            
            # --- ПЕРЕХВАТ ОШИБОК ВЫПОЛНЕНИЯ ---
            try:
                await func(event, *args, **kwargs)
            except Exception:
                # Если произошла ошибка во время выполнения команды
                from utils.message_builder import build_and_edit
                
                exc = traceback.format_exc()
                
                parts = [
                    {"text": "🚫 Call "},
                    {"text": f".{command}", "entity": MessageEntityCode},
                    {"text": " failed!\n\n", "entity": MessageEntityBold},
                    {"text": "🧾 Logs:\n", "entity": MessageEntityBold},
                    # Попытка создать свернутую цитату.
                    # message_builder должен уметь обрабатывать kwargs или игнорировать их при ошибке.
                    {
                        "text": exc, 
                        "entity": MessageEntityBlockquote, 
                        "kwargs": {"collapsed": True}
                    }
                ]
                
                try:
                    await build_and_edit(event, parts)
                except Exception as e:
                    print(f"CRITICAL ERROR in .{command} handler:\n{exc}")
                    print(f"Failed to send error message: {e}")
            # ----------------------------------
        
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
    try:
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

def _find_module_path(module_name: str) -> Path | None:
    # Ищем путь к файлу модуля по его имени импорта (например, "admin" -> modules/admin.py)
    # Это упрощенная логика, но для modules/* она работает.
    potential_path = MODULES_DIR / f"{module_name}.py"
    if potential_path.exists():
        return potential_path
    
    # Проверка пакетов (папок)
    potential_dir = MODULES_DIR / module_name / "__init__.py"
    if potential_dir.exists():
        return potential_dir.parent
    
    return None

async def load_module(client, module_name: str, chat_id: int = None) -> dict:
    """Загружает модуль и регистрирует его обработчики."""
    # Проверяем оба варианта ключа: обычный и heroku:
    _already_keys = [module_name, f"heroku:{module_name}"]
    if any(k in client.modules for k in _already_keys):
        return {"status": "info", "message": f"Модуль {module_name} уже загружен."}
    # Также проверяем heroku-модули у которых file_name совпадает с module_name
    for _k, _v in getattr(client, "modules", {}).items():
        if _k.startswith("heroku:") and _v.get("file_name", "") == module_name:
            return {"status": "info", "message": f"Модуль {module_name} уже загружен как {_k}."}

    try:
        # --- INTEGRITY CHECK (PRE-LOAD) ---
        from utils.integrity import verify_integrity
        integrity_status = verify_integrity()
        if integrity_status["status"] == "compromised":
            return {
                "status": "error",
                "message": f"🚨 КРИТИЧЕСКАЯ УГРОЗА: Файл {integrity_status['file']} был изменен! Система безопасности нарушена. Загрузка отменена."
            }
        # ----------------------------------

        # --- АНАЛИЗ ФАЙЛА: безопасность + определение типа ---
        TRUSTED_SYSTEM_MODULES = ["install", "modules", "updater", "core_updater"]
        from utils.security import scan_code
        module_path = _find_module_path(module_name)
        _is_heroku_mod = False

        if module_path and module_path.is_file():
            try:
                with open(module_path, 'r', encoding='utf-8') as f:
                    _file_content = f.read()

                # Определяем Heroku-модуль по реальным импортам через AST
                # (строковый поиск ненадёжен — install.py содержит эти строки в своём коде)
                import ast as _ast
                try:
                    _tree = _ast.parse(_file_content)
                    # Фреймворки, чьи модули запускаются через heroku_loader
                    _COMPAT_FRAMEWORKS = {"herokutl", "hikka", "dragon", "watgbridge"}
                    for _node in _ast.walk(_tree):
                        if isinstance(_node, _ast.ImportFrom):
                            # from .. import loader  (relative, level >= 1)
                            if _node.level and _node.level >= 1:
                                names = [a.name for a in _node.names]
                                if "loader" in names or "utils" in names:
                                    _is_heroku_mod = True
                                    break
                            # from hikka import loader / from herokutl.xxx import yyy
                            if _node.module:
                                _mod_root = _node.module.split(".")[0]
                                if _mod_root in _COMPAT_FRAMEWORKS:
                                    _is_heroku_mod = True
                                    break
                        if isinstance(_node, _ast.Import):
                            for _a in _node.names:
                                _imp_root = _a.name.split(".")[0]
                                if _imp_root in _COMPAT_FRAMEWORKS:
                                    _is_heroku_mod = True
                                    break
                except Exception:
                    pass

                # Сканер безопасности — только для обычных модулей
                if not _is_heroku_mod and module_name not in TRUSTED_SYSTEM_MODULES:
                    scan_result = scan_code(_file_content)
                    if scan_result["level"] == "block":
                        blocked_path = str(module_path) + ".blocked"
                        os.rename(module_path, blocked_path)
                        return {
                            "status": "error",
                            "message": f"🚫 ЗАЩИТА: Модуль {module_name} заблокирован!\nОбнаружены критические угрозы: {scan_result['reasons']}\nФайл переименован в .blocked"
                        }
            except Exception as e:
                print(f"Module analysis failed for {module_name}: {e}")

        # Heroku/Hikka-модуль — передаём в compat-загрузчик
        if _is_heroku_mod:
            from compat.heroku_loader import load_heroku_module
            return await load_heroku_module(client, module_path, chat_id)
        # ------------------------------------------------------

        import_name = f"modules.{module_name}"

        if import_name in sys.modules:
            importlib.reload(sys.modules[import_name])
        imported_module = importlib.import_module(import_name)

        registered_handlers = []
        module_instance = None
        for name, obj in inspect.getmembers(imported_module, inspect.isclass):
            if issubclass(obj, Module) and obj is not Module:
                module_instance = obj()
                from utils.security import get_safe_client
                safe_client = get_safe_client(client)
                module_instance.client = safe_client
                from utils import database
                module_instance.db = database
                if hasattr(module_instance, "client_ready"):
                    await module_instance.client_ready(safe_client, database)
                break
        search_target = module_instance if module_instance else imported_module
        
        for name, func in inspect.getmembers(search_target):
            if not (inspect.isfunction(func) or inspect.ismethod(func)):
                continue

            if getattr(func, "_is_command", False):
                command_name, handler_args, doc = func._command_name, func._command_kwargs, func._command_doc
                pattern_text = re.escape(PREFIX) + command_name + r"(?:\s+(.*))?$"
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
        
        # --- INTEGRITY CHECK (POST-LOAD) ---
        # Проверяем, не изменил ли модуль системные файлы при импорте
        integrity_status = verify_integrity()
        if integrity_status["status"] == "compromised":
            # Если модуль сломал систему - удаляем его из памяти и блокируем файл
            if import_name in sys.modules: del sys.modules[import_name]
            if module_path and module_path.exists():
                os.rename(module_path, str(module_path) + ".malware")
            
            return {
                "status": "error",
                "message": f"🚨 АТАКА ОБНАРУЖЕНА: Модуль {module_name} попытался изменить ядро ({integrity_status['file']})! Модуль заблокирован и переименован в .malware."
            }
        # -----------------------------------
        
        return {"status": "ok", "message": f"Модуль {module_name} успешно загружен."}

    except Exception as e:
        # Возвращаем traceback для modules.py
        return {
            "status": "error", 
            "message": f"Ошибка при загрузке {module_name}:\n{e}", 
            "traceback": traceback.format_exc()
        }

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

        for pattern in list(CALLBACK_REGISTRY):
            if CALLBACK_REGISTRY[pattern].__module__ == f"modules.{module_name}":
                del CALLBACK_REGISTRY[pattern]
        
        for pattern in list(INLINE_HANDLERS_REGISTRY):
            if INLINE_HANDLERS_REGISTRY[pattern]["func"].__module__ == f"modules.{module_name}":
                del INLINE_HANDLERS_REGISTRY[pattern]

        del client.modules[module_name]

        for name in list(sys.modules):
            if name == f"modules.{module_name}" or name.startswith(f"modules.{module_name}."):
                del sys.modules[name]

        return {"status": "ok", "message": f"Модуль {module_name} успешно выгружен."}
    except Exception as e:
        return {
            "status": "error", 
            "message": f"Ошибка при выгрузке {module_name}:\n{e}",
            "traceback": traceback.format_exc()
        }

async def reload_module(client, module_name: str, chat_id: int = None) -> dict:
    unload_result = await unload_module(client, module_name)
    if unload_result["status"] == "error":
        return unload_result
    
    return await load_module(client, module_name, chat_id)

async def register_single_alias(client, alias: str, real_command: str, module_name: str):
    """Регистрирует один алиас. Используется в modules/aliases.py"""
    if module_name not in client.modules: return False
    
    module_data = client.modules[module_name]
    target_func = None
    
    for func, handler in module_data["handlers"]:
        if getattr(func, "_command_name", None) == real_command:
            target_func = func
            handler_args = getattr(func, "_command_kwargs", {}).copy()
            break
    
    if target_func:
        pattern_text = re.escape(PREFIX) + re.escape(alias) + r"(?:\s+(.*))?$"
        handler_args["pattern"] = re.compile(pattern_text, re.IGNORECASE | re.DOTALL)
        
        new_handler = events.NewMessage(**handler_args)
        client.add_event_handler(target_func, new_handler)
        
        # Добавляем в список хендлеров модуля
        client.modules[module_name]["handlers"].append((target_func, new_handler))
        
        # Добавляем в реестр команд для .help
        if alias not in COMMANDS_REGISTRY: COMMANDS_REGISTRY[alias] = []
        original_doc = "Нет описания"
        if real_command in COMMANDS_REGISTRY:
            original_doc = COMMANDS_REGISTRY[real_command][0].get("doc", "Нет описания")
        
        COMMANDS_REGISTRY[alias].append({
            "module": module_name,
            "doc": f"🔗 Алиас для .{real_command}\n\n{original_doc}"
        })
        return True
    return False

async def register_aliases(client):
    """
    Регистрирует ВСЕ алиасы из базы данных.
    """
    from utils import database as db
    aliases = db.get_all_aliases()
    count = 0
    for row in aliases:
        if await register_single_alias(client, row['alias'], row['real_command'], row['module_name']):
            count += 1
    return count

def get_all_modules(client=None) -> list[str]:
    """Возвращает список модулей из папки modules/.
    
    Если передан client — исключает файлы которые уже загружены
    как heroku-модули (по file_name), чтобы они не дублировались
    в списке обычных модулей.
    """
    # Собираем file_name всех загруженных heroku-модулей
    _heroku_file_names = set()
    if client is not None:
        for _k, _v in getattr(client, "modules", {}).items():
            if _k.startswith("heroku:") and isinstance(_v, dict):
                fn = _v.get("file_name", "")
                if fn:
                    _heroku_file_names.add(fn.lower())

    all_modules = []
    for path in MODULES_DIR.rglob("*.py"):
        if path.name.startswith("_"):
            continue
        relative_path = path.relative_to(MODULES_DIR)
        import_path = ".".join(relative_path.with_suffix("").parts)
        # Исключаем файлы уже загруженные как heroku-модули
        stem = path.stem.lower()
        if stem in _heroku_file_names:
            continue
        all_modules.append(import_path)
    return all_modules
