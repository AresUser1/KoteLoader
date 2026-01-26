# utils/loader.py

import importlib
import sys
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
    # Принудительно слушаем всё (и вход и выход), фильтрация теперь внутри wrapper
    kwargs["incoming"] = True
    kwargs["outgoing"] = True
    
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # 1. Ищем объект события
            event = None
            for arg in args:
                if hasattr(arg, 'sender_id') and hasattr(arg, 'client'):
                    event = arg
                    break
            
            if not event:
                return await func(*args, **kwargs)

            from utils import database as db
            
            sender_id = event.sender_id
            level = db.get_user_level(sender_id)
            
            # 2. ПРОВЕРКА ДОСТУПА
            allowed_to_run = False
            
            # Владелец всегда может всё (проверяем по event.out или по БД)
            if event.out or level == "OWNER":
                allowed_to_run = True
            
            # TRUSTED - проверяем список модулей
            elif level == "TRUSTED":
                mod_path = func.__module__
                module_name = mod_path.split('.')[-1].lower()
                
                if module_name in ["help", "about"]:
                    allowed_to_run = True
                else:
                    allowed = db.get_setting(f"allowed_mods_{sender_id}")
                    if not allowed:
                        allowed = db.get_setting("allowed_mods_TRUSTED", default="wisp")
                    
                    if allowed.lower() == "all":
                        allowed_to_run = True
                    else:
                        allowed_list = [m.strip().lower() for m in allowed.split(",")]
                        if module_name in allowed_list:
                            allowed_to_run = True
                        else:
                            print(f"🛑 [Access Denied] User {sender_id} -> .{command} (mod: {module_name})")
            
            if not allowed_to_run:
                return

            # 3. ПРОВЕРКА ВКЛЮЧЕННОСТИ
            is_enabled = db.get_setting("userbot_enabled", default="True") == "True"
            if not is_enabled and not (level == "OWNER" and command == "on"):
                return
            
            # --- ВЫПОЛНЕНИЕ ---
            print(f"🟢 [Running] .{command} for {sender_id} (Level: {level})")
            try:
                return await func(*args, **kwargs)
            except Exception:
                from utils.message_builder import build_and_edit
                exc = traceback.format_exc()
                print(f"❌ Error in .{command}:\n{exc}")
                try:
                    parts = [
                        {"text": "🚫 Call "},
                        {"text": f".{command}", "entity": MessageEntityCode},
                        {"text": " failed!\n\n", "entity": MessageEntityBold},
                        {"text": "🧾 Logs:\n", "entity": MessageEntityBold},
                        {"text": exc, "entity": MessageEntityBlockquote, "kwargs": {"collapsed": True}}
                    ]
                    await build_and_edit(event, parts)
                except: pass
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

async def load_module(client, module_name: str, chat_id: int = None) -> dict:
    """Загружает модуль и регистрирует его обработчики."""
    if module_name in client.modules:
        return {"status": "info", "message": f"Модуль {module_name} уже загружен."}

    try:
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
            "handlers": registered_handlers,
            "alias_handlers": [] 
        }
        
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
        
        for func, handler in module_data.get("alias_handlers", []):
            client.remove_event_handler(func, handler)

        for command in list(COMMANDS_REGISTRY):
            COMMANDS_REGISTRY[command] = [cmd for cmd in COMMANDS_REGISTRY[command] if cmd["module"] != module_name]
            if not COMMANDS_REGISTRY[command]: del COMMANDS_REGISTRY[command]
        
        # Также чистим алиасы из реестра
        aliases_to_remove = [alias for alias, details in COMMANDS_REGISTRY.items() if details and details[0].get("is_alias") and details[0].get("module") == module_name]
        for alias in aliases_to_remove:
            del COMMANDS_REGISTRY[alias]

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
    
    load_result = await load_module(client, module_name, chat_id)
    if load_result["status"] == 'ok':
        # После перезагрузки одного модуля, нужно перерегистрировать все алиасы,
        # так как могли измениться ссылки на функции
        await register_aliases(client)
    return load_result

def get_all_modules() -> list[str]:
    all_modules = []
    for path in MODULES_DIR.rglob("*.py"):
        if path.name.startswith("_"):
            continue
        relative_path = path.relative_to(MODULES_DIR)
        import_path = ".".join(relative_path.with_suffix("").parts)
        all_modules.append(import_path)
    return all_modules


# --- ALIAS LOGIC ---

async def register_single_alias(client, alias: str, real_command: str, module_name: str):
    """Находит и регистрирует один конкретный алиас."""
    if module_name not in client.modules:
        return
        
    original_func = None
    handler_args = None

    # Ищем оригинальную функцию и ее аргументы
    for func, handler in client.modules[module_name]["handlers"]:
        if getattr(func, "_is_command", False) and func._command_name == real_command:
            original_func = func
            handler_args = func._command_kwargs.copy()
            break
            
    if not original_func or not handler_args:
        return

    # Создаем новый обработчик для алиаса
    pattern_text = re.escape(PREFIX) + alias + r"(?:\s+(.*))?$"
    handler_args["pattern"] = re.compile(pattern_text, re.IGNORECASE | re.DOTALL)
    
    # Убеждаемся, что wrapper-функция будет уникальной, чтобы telethon не ругался
    async def alias_wrapper(event):
        await original_func(event)

    alias_handler = events.NewMessage(**handler_args)
    client.add_event_handler(alias_wrapper, alias_handler)
    
    # Сохраняем ссылку на обработчик, чтобы его можно было удалить
    client.modules[module_name]["alias_handlers"].append((alias_wrapper, alias_handler))
    
    # Добавляем алиас в реестр для .help и других систем
    if alias not in COMMANDS_REGISTRY: COMMANDS_REGISTRY[alias] = []
    COMMANDS_REGISTRY[alias].append({
        "module": module_name, 
        "doc": f"<i>Псевдоним для</i> <code>{real_command}</code>",
        "is_alias": True
    })

async def unregister_single_alias(client, alias_to_remove: str):
    """Находит и удаляет обработчик для одного алиаса."""
    found_and_removed = False
    for mod_name, mod_data in client.modules.items():
        handlers_to_keep = []
        for func, handler in mod_data.get("alias_handlers", []):
            # Паттерн хранится в handler.pattern
            pattern = handler.pattern.pattern
            # Собираем ожидаемый паттерн для алиаса
            expected_pattern = re.escape(PREFIX) + alias_to_remove + r"(?:\s+(.*))?$"
            if pattern == expected_pattern:
                client.remove_event_handler(func, handler)
                found_and_removed = True
            else:
                handlers_to_keep.append((func, handler))
        
        if found_and_removed:
            mod_data["alias_handlers"] = handlers_to_keep
            break # Алиас уникален, можно выходить

    # Удаляем из реестра команд
    if alias_to_remove in COMMANDS_REGISTRY:
        COMMANDS_REGISTRY[alias_to_remove] = [
            cmd for cmd in COMMANDS_REGISTRY[alias_to_remove] if not cmd.get("is_alias")
        ]
        if not COMMANDS_REGISTRY[alias_to_remove]:
            del COMMANDS_REGISTRY[alias_to_remove]


async def register_aliases(client):
    """Загружает все алиасы из БД и регистрирует для них обработчики."""
    from utils import database as db
    
    # Сначала очистим все старые обработчики алиасов
    for module_data in client.modules.values():
        for func, handler in module_data.get("alias_handlers", []):
            client.remove_event_handler(func, handler)
        module_data["alias_handlers"] = []

    # И почистим реестр
    for command in list(COMMANDS_REGISTRY):
        COMMANDS_REGISTRY[command] = [cmd for cmd in COMMANDS_REGISTRY[command] if not cmd.get("is_alias")]
        if not COMMANDS_REGISTRY[command]: del COMMANDS_REGISTRY[command]

    aliases = db.get_all_aliases()
    if not aliases: return

    print(f"🔹 Регистрирую {len(aliases)} псевдонимов...")
    for item in aliases:
        await register_single_alias(client, item['alias'], item['real_command'], item['module_name'])