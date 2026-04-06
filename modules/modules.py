# modules/modules.py
"""
<manifest>
version: 2.0.4
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/modules.py
author: Kote
</manifest>

Управление модулями: загрузка, выгрузка, перезагрузка и просмотр информации.
Включает защиту системных модулей от выгрузки.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
import re
from core import register
from utils import database as db
from services.module_info_cache import parse_manifest
from utils.loader import get_all_modules, COMMANDS_REGISTRY, load_module, unload_module, reload_module
from services.state_manager import update_state_file
from utils.message_builder import build_and_edit, build_message
from utils.security import check_permission
from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityBold, MessageEntityCode, MessageEntityItalic, MessageEntityBlockquote
from telethon.errors.rpcerrorlist import MessageNotModifiedError

MODULES_DIR = Path(__file__).parent.parent / "modules"
BACKUPS_DIR = Path(__file__).parent.parent / "backups"

# --- СПИСОК ЗАЩИЩЕННЫХ МОДУЛЕЙ ---
# Эти модули нельзя выгрузить (.unload) и нельзя скачать (.getm)
PROTECTED_MODULES = []

# Используем тот же список для отображения в разделе "Системные"
SYSTEM_MODULE_NAMES = PROTECTED_MODULES

def _get_static_emojis() -> dict:
    DEFAULT_STATIC_EMOJIS = {
        "PACKAGE":    {"id": 5256094480498436162, "fallback": "📦"},
        "SETTINGS":   {"id": 5253952855185829086, "fallback": "⚙️"},
        "WRENCH":     {"id": 5258023599419171861, "fallback": "🔧"},
        "CHART":      {"id": 5364265190353286344, "fallback": "📊"},
        "SEARCH":     {"id": 5258274739041883702, "fallback": "🔍"},
        "INFO":       {"id": 5256230583717079814, "fallback": "📋"},
        "UPDATE":     {"id": 5877410604225924969, "fallback": "🔄"},
        "ERROR":      {"id": 5985346521103604145, "fallback": "❌"},
        "SUCCESS":    {"id": 5255813619702049821, "fallback": "✅"},
        "DB":         {"id": 5884479287171485878, "fallback": "🗄️"},
        "CALENDAR":   {"id": 5967412305338568701, "fallback": "📅"},
        "ROCKET":     {"id": 5445284980978621387, "fallback": "🚀"},
        "VERSION":    {"id": 5843862283964390528, "fallback": "🔖"},
        "DESC":       {"id": 6028435952299413210, "fallback": "ℹ️"},
        "SOURCE":     {"id": 5924720918826848520, "fallback": "📦"},
        "AUTHOR":     {"id": 6032608126480421344, "fallback": "👤"},
        "LOCK":       {"id": 5778570255555105942, "fallback": "🔒"}
    }
    custom_emojis = db.get_module_data("modules", "modules_emojis", default={})
    return {**DEFAULT_STATIC_EMOJIS, **custom_emojis}

def _build_emoji_part(emoji_details: dict) -> dict:
    part = {"text": emoji_details.get('fallback', '❔')}
    if emoji_details.get('id') != 0:
        part["entity"] = MessageEntityCustomEmoji
        part["kwargs"] = {"document_id": emoji_details['id']}
    return part

def get_static_mod_emoji_data(key: str) -> dict:
    all_emojis = _get_static_emojis()
    return all_emojis.get(key.upper(), {"id": 0, "fallback": "?"})

def _normalize(s: str) -> str:
    """Нормализует имя модуля для сравнения: lower + убираем пробелы/точки/тире/цифры не трогаем."""
    import re
    return re.sub(r"[^a-zа-я0-9]", "", s.lower())

def _find_module_by_name(user_input: str, client=None) -> str | None:
    """
    Ищет модуль по имени. Поддерживает:
    - Имена файлов (tagall2_0, sysinfo)
    - Имена из strings["name"] в client.modules (TagAll 2.0, GoyPulse V9)
    - Нечёткое совпадение без спецсимволов
    """
    if not user_input: return None

    # 1. Точное совпадение с именем файла
    all_modules = get_all_modules()
    if user_input in all_modules: return user_input

    user_lower = user_input.lower()
    user_norm = _normalize(user_input)

    # 2. Совпадение с именем файла без учёта регистра
    for mod_name in all_modules:
        if mod_name.lower() == user_lower: return mod_name

    # 3. Нормализованное совпадение с именем файла (убираем все спецсимволы)
    for mod_name in all_modules:
        if _normalize(mod_name) == user_norm: return mod_name

    return None

def _find_module_by_name_with_client(user_input: str, client) -> str | None:
    """
    Расширенный поиск: включает имена из client.modules (heroku-модули).
    Всегда возвращает имя файла (stem) если возможно, иначе bare-имя из ключа.
    """
    # 1. Обычный поиск по именам файлов
    found = _find_module_by_name(user_input, client)
    if found: return found

    if not client: return None

    user_norm = _normalize(user_input)
    mods = getattr(client, "modules", {})

    # 2. Ищем по нормализованному имени в ключах client.modules
    for key, val in mods.items():
        bare = key.replace("heroku:", "").replace("Heroku:", "")
        if _normalize(bare) == user_norm or _normalize(key) == user_norm:
            # Пытаемся найти реальный файл модуля
            # Сначала через instance.__module__ или путь
            instance = val.get("instance") if isinstance(val, dict) else None
            if instance:
                mod = val.get("module") if isinstance(val, dict) else None
                if mod and hasattr(mod, "__file__") and mod.__file__:
                    from pathlib import Path
                    return Path(mod.__file__).stem
            # Иначе ищем файл по нормализованному имени
            for p in MODULES_DIR.rglob("*.py"):
                if _normalize(p.stem) == user_norm:
                    return p.stem
            # Последний fallback — возвращаем bare как есть
            return bare

    return None

async def _parse_emoji_args(event, cmd_name: str, example_key: str) -> dict:
    prefix = db.get_setting('prefix', '.')
    args_str = event.pattern_match.group(1)
    fallback_char = "❔"
    args_before_pipe = args_str
    if "|" in (args_str or ""):
        parts = args_str.split("|", 1)
        args_before_pipe = parts[0].strip()
        fallback_text = parts[1].strip()
        if fallback_text: fallback_char = fallback_text[0]
    if not args_before_pipe:
        return {"error": [
            {"text": "❌ Неверный формат!\n"},
            {"text": f"Пример: {prefix}{cmd_name} {example_key} ", "entity": MessageEntityCode},
            {"text": "ID ", "entity": MessageEntityBold},
            {"text": "| ", "entity": MessageEntityCode},
            {"text": "X", "entity": MessageEntityBold}
        ]}
    parts = args_before_pipe.split()
    key = parts[0]
    emoji_id = 0
    if event.entities:
        for entity in event.entities:
            try:
                args_start_index = event.text.find(args_str)
                min_emoji_offset = event.text.find(key) + len(key)
            except:
                min_emoji_offset = len(prefix) + len(cmd_name) + len(key) + 2 
            if isinstance(entity, MessageEntityCustomEmoji) and entity.offset >= min_emoji_offset:
                emoji_id = entity.document_id
                if fallback_char == "❔":
                    try:
                        entity_text_utf16 = event.text.encode('utf-16-le')
                        start, end = entity.offset * 2, (entity.offset + entity.length) * 2
                        fb = entity_text_utf16[start:end].decode('utf-16-le')[0]
                        if fb.strip(): fallback_char = fb
                    except Exception: pass
                return {"key": key, "id": emoji_id, "fallback": fallback_char}
    if len(parts) > 1:
        try: emoji_id = int(parts[1])
        except (ValueError, TypeError): return {"error": [{"text": "❌ ID должен быть числом"}]}
    else: return {"error": [{"text": "❌ Укажите ID или Премиум-Эмодзи"}]}
    if fallback_char == "❔" and emoji_id != 0:
         fallback_char = args_before_pipe.split(maxsplit=2)[-1][0] if len(args_before_pipe.split()) > 1 else '✨'
         if fallback_char == "❔" or fallback_char.isdigit():
             return {"error": [{"text": "❌ Укажите fallback-символ после |"}]}
    return {"key": key, "id": emoji_id, "fallback": fallback_char}

@register("setmodemoji", incoming=True)
async def setmodemoji_cmd(event):
    """Устанавливает кастомный статичный эмодзи."""
    if not check_permission(event, min_level="TRUSTED"): return
    parsed = await _parse_emoji_args(event, "setmodemoji", "PACKAGE")
    if "error" in parsed: return await build_and_edit(event, parsed["error"])
    key_upper = parsed["key"].upper()
    if key_upper not in _get_static_emojis():
        return await build_and_edit(event, [{"text": "❌ Неизвестный ключ" }])
    custom_emojis = db.get_module_data("modules", "modules_emojis", default={})
    custom_emojis[key_upper] = {"id": parsed["id"], "fallback": parsed["fallback"]}
    db.set_module_data("modules", "modules_emojis", custom_emojis)
    await build_and_edit(event, [{"text": "✅ "}, {"text": f"Эмодзи для {key_upper} (в modules.py) установлен!", "entity": MessageEntityBold}])

@register("delmodemoji", incoming=True)
async def delmodemoji_cmd(event):
    """Сбрасывает статичный эмодзи."""
    if not check_permission(event, min_level="TRUSTED"): return
    key_upper = (event.pattern_match.group(1) or "").upper()
    if not key_upper: return await build_and_edit(event, [{"text": "❌ Укажите ключ"}])
    custom_emojis = db.get_module_data("modules", "modules_emojis", default={})
    if key_upper in custom_emojis:
        del custom_emojis[key_upper]
        db.set_module_data("modules", "modules_emojis", custom_emojis)
        await build_and_edit(event, [{"text": "🗑️ Эмодзи сброшены."}])
    else:
        await build_and_edit(event, [{"text": "ℹ️ Эмодзи не был найден."}])

@register("modemojis", incoming=True)
async def modemojis_cmd(event):
    """Показывает текущие настройки эмодзи."""
    if not check_permission(event, min_level="TRUSTED"): return
    parts = [{"text": "⚙️ "}, {"text": "Эмодзи для "}, {"text": "modules.py", "entity": MessageEntityCode}, {"text": "\n(Кастомные из БД перезаписывают дефолтные)\n\n"}]
    mapping = _get_static_emojis()
    custom_keys = db.get_module_data("modules", "modules_emojis", default={}).keys()
    for key, details in sorted(mapping.items()):
        is_custom = " (кастомный)" if key in custom_keys else ""
        parts.append(_build_emoji_part(details))
        parts.append({"text": f" {key}{is_custom}: ", "entity": MessageEntityBold})
        if details['id'] != 0: parts.append({"text": str(details['id']), "entity": MessageEntityCode})
        else: parts.append({"text": "ID не задан", "entity": MessageEntityItalic})
        parts.append({"text": "\n"})
    await build_and_edit(event, parts)

@register("modules", incoming=True)
async def list_modules(event):
    """Показывает детальный список всех модулей."""
    if not check_permission(event, min_level="TRUSTED"): return
    emojis = _get_static_emojis()
    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=1)
    search_query = args[1].lower() if len(args) > 1 else None
    all_modules = get_all_modules(event.client)
    _raw_mods = event.client.modules if hasattr(event.client, 'modules') else {}
    loaded_modules = set(_raw_mods.keys())
    # Строим сет нормализованных имён загруженных модулей,
    # включая file_name из heroku-модулей ("goypulse" матчит "heroku:GoyPulse V9")
    _loaded_norms = set()
    for _k, _v in _raw_mods.items():
        _loaded_norms.add(_normalize(_k.replace("heroku:", "").replace("Heroku:", "")))
        if isinstance(_v, dict) and _v.get("file_name"):
            _loaded_norms.add(_normalize(_v["file_name"]))

    if search_query: all_modules = [mod for mod in all_modules if search_query in mod.lower()]
    
    if not all_modules:
        query_text = f" по запросу '{search_query}'" if search_query else ""
        return await build_and_edit(event, [_build_emoji_part(emojis['PACKAGE']), {"text": f"Модули{query_text} не найдены.", "entity": MessageEntityBold}])
    
    system_modules, user_modules = [], []
    for module in sorted(all_modules):
        _is_loaded = (module in loaded_modules
                      or f"heroku:{module}" in loaded_modules
                      or _normalize(module) in _loaded_norms)
        info = {'name': module, 'loaded': _is_loaded, 'commands': get_module_commands(module, event.client), 'size': get_module_size(module)}
        (system_modules if module.lower() in SYSTEM_MODULE_NAMES else user_modules).append(info)
    
    parts = [_build_emoji_part(emojis['PACKAGE']), {"text": "Управление модулями", "entity": MessageEntityBold}, {"text": "\n\n"}]
    if search_query: parts.extend([_build_emoji_part(emojis['SEARCH']), {"text": f" Результаты поиска: "}, {"text": f"{search_query}", "entity": MessageEntityCode}, {"text": "\n\n"}])
    
    def format_section(modules_list, title, emoji_details):
        if not modules_list: return
        parts.append(_build_emoji_part(emoji_details))
        parts.extend([{"text": f" {title}", "entity": MessageEntityBold}, {"text": f" ({len(modules_list)}):\n"}])
        for mod in modules_list:
            status_emoji = "✅" if mod['loaded'] else "❌"
            # Добавляем 🔒 если модуль защищен
            lock_icon = "🔒" if mod['name'] in PROTECTED_MODULES else ""
            cmd_count, size_kb = len(mod['commands']), mod['size']
            parts.append({"text": f"{status_emoji} "})
            parts.append({"text": f"{mod['name']}", "entity": MessageEntityCode})
            if lock_icon: parts.append({"text": f" {lock_icon}"})
            if cmd_count > 0: parts.append({"text": f" • {cmd_count} cmd"})
            if size_kb: parts.append({"text": f" • {size_kb} KB"})
            parts.append({"text": "\n"})
        parts.append({"text": "\n"})
    
    format_section(system_modules, "Системные (Защищенные)", emojis['SETTINGS'])
    format_section(user_modules, "Пользовательские модули", emojis['WRENCH'])
    
    total_commands = sum(len(get_module_commands(m, event.client)) for m in all_modules)
    # Считаем реально загруженные с той же логикой нормализации что используется выше
    _actually_loaded = sum(
        1 for m in all_modules
        if (m in loaded_modules
            or f"heroku:{m}" in loaded_modules
            or _normalize(m) in _loaded_norms)
    )
    parts.extend([_build_emoji_part(emojis['CHART']), {"text": " Статистика:", "entity": MessageEntityBold}, {"text": "\n"}, {"text": f"• Всего модулей: {len(all_modules)}\n"}, {"text": f"• Загружено: {_actually_loaded}/{len(all_modules)}\n"}, {"text": f"• Команд доступно: {total_commands}"}])
    await build_and_edit(event, parts)

@register("minfo", incoming=True)
async def module_info(event):
    """Показывает подробную информацию о модуле."""
    if not check_permission(event, min_level="TRUSTED"): return
    emojis = _get_static_emojis()
    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=1)
    if len(args) < 2:
        return await build_and_edit(event, [_build_emoji_part(emojis['INFO']), {"text": " Укажите имя модуля:\n", "entity": MessageEntityBold}, {"text": f"{prefix}minfo <module_name>", "entity": MessageEntityCode}])
    
    module_name_input = args[1].strip()
    module_name = _find_module_by_name_with_client(module_name_input, event.client)
    
    if not module_name:
         return await build_and_edit(event, [_build_emoji_part(emojis['ERROR']), {"text": " Модуль "}, {"text": module_name_input, "entity": MessageEntityCode}, {"text": " не найден.", "entity": MessageEntityBold}])

    module_path = None
    potential_paths = list(MODULES_DIR.rglob(f"{module_name.replace('.', '/')}.py"))
    if potential_paths: module_path = potential_paths[0]

    # Fallback: ищем файл по всем вариантам имени (tagall2_0, tagall2.0 и т.д.)
    if not module_path or not module_path.exists():
        import re as _re
        norm = _re.sub(r'[^a-zA-Zа-яА-Я0-9]', '', module_name)
        for p in MODULES_DIR.rglob('*.py'):
            if _re.sub(r'[^a-zA-Zа-яА-Я0-9]', '', p.stem) == norm:
                module_path = p
                break

    if not module_path or not module_path.exists():
        return await build_and_edit(event, [_build_emoji_part(emojis['ERROR']), {"text": " Модуль "}, {"text": module_name, "entity": MessageEntityCode}, {"text": " не найден (ошибка пути).", "entity": MessageEntityBold}])
    
    manifest = parse_manifest(module_path.read_text(encoding='utf-8'))
    
    parts = [_build_emoji_part(emojis['INFO']), {"text": " Информация о модуле ", "entity": MessageEntityBold}, {"text": module_name, "entity": MessageEntityCode}, {"text": "\n\n"}]
    
    if module_name in PROTECTED_MODULES:
        parts.append(_build_emoji_part(emojis['LOCK']))
        parts.append({"text": " Этот модуль защищен (Системный)\n\n", "entity": MessageEntityBold})

    if manifest["description"]:
        parts.append(_build_emoji_part(emojis['DESC']))
        parts.extend([{"text": " Описание:\n", "entity": MessageEntityBold}, {"text": manifest["description"], "entity": MessageEntityItalic}, {"text": "\n\n"}])
    
    parts.extend([_build_emoji_part(emojis['VERSION']), {"text": " Версия: ", "entity": MessageEntityBold}, {"text": f"{manifest.get('version', 'N/A')}\n"}, _build_emoji_part(emojis['SOURCE']), {"text": " Источник: ", "entity": MessageEntityBold}, {"text": f"{manifest.get('source', 'N/A')}\n"}, _build_emoji_part(emojis['AUTHOR']), {"text": " Автор: ", "entity": MessageEntityBold}, {"text": f"{manifest.get('author', 'Неизвестно')}\n\n"}])
    
    size_kb = round(module_path.stat().st_size / 1024, 2)
    mtime = datetime.fromtimestamp(module_path.stat().st_mtime)
    _mods = getattr(event.client, 'modules', {})
    _mod_norm = _normalize(module_name)
    loaded = (module_name in _mods
              or f"heroku:{module_name}" in _mods
              or any(_normalize(_k.replace("heroku:", "").replace("Heroku:", "")) == _mod_norm
                     for _k in _mods)
              # Матчим по file_name (имя файла без .py), которое heroku_loader сохраняет в значении.
              # Нужно для модулей у которых strings["name"] отличается от имени файла,
              # напр. "goypulse.py" но strings["name"] = "GoyPulse V9" → "goypulsev9" != "goypulse"
              or any(_normalize(v.get("file_name", "")) == _mod_norm
                     for v in _mods.values() if isinstance(v, dict)))
    
    parts.extend([_build_emoji_part(emojis['CHART']), {"text": f" Размер: {size_kb} KB\n"}, _build_emoji_part(emojis['CALENDAR']), {"text": f" Изменен: {mtime.strftime('%d.%m.%Y %H:%M')}\n"}, _build_emoji_part(emojis['UPDATE']), {"text": " Статус: ", "entity": MessageEntityBold}, (_build_emoji_part(emojis['SUCCESS']) if loaded else _build_emoji_part(emojis['ERROR'])), {"text": " Загружен\n\n" if loaded else " Не загружен\n\n"}])
    
    commands = get_module_commands(module_name, event.client)
    if commands:
        parts.extend([_build_emoji_part(emojis['WRENCH']), {"text": f" Команды ({len(commands)}):\n", "entity": MessageEntityBold}])
        _mn_lower = module_name.lower()
        for cmd in sorted(commands):
            # Ищем запись именно для этого модуля (не [0] который может быть чужим)
            _entries = COMMANDS_REGISTRY.get(cmd, [])
            _doc = ""
            for _e in _entries:
                _lbl = _e.get("module", "")
                _bare = _lbl.lower().replace("heroku:", "")
                if (_lbl == module_name or _lbl == f"heroku:{module_name}"
                        or _bare == _mn_lower or _bare.startswith(_mn_lower)):
                    _doc = _e.get("doc", "")
                    break
            short_desc = _doc.split('\n')[0][:50] if _doc else "Нет описания"
            parts.extend([{"text": "• "}, {"text": f"{prefix}{cmd}", "entity": MessageEntityCode}, {"text": f" - {short_desc}\n"}])
        parts.append({"text": "\n"})
    
    db_configs = db.get_all_module_configs(module_name)
    db_data = db.get_all_module_data(module_name)
    if db_configs or db_data:
        parts.extend([_build_emoji_part(emojis['DB']), {"text": " Данные в БД:\n", "entity": MessageEntityBold}])
        if db_configs: parts.append({"text": f"• Настроек: {len(db_configs)}\n"})
        if db_data: parts.append({"text": f"• Записей данных: {len(db_data)}\n"})
    
    await build_and_edit(event, parts, link_preview=False)

async def _handle_module_command(event, action: str):
    """Общий обработчик для load/unload/reload."""
    if not check_permission(event, min_level="TRUSTED"): return
    prefix = db.get_setting("prefix", default=".")
    module_name_input = event.pattern_match.group(1)
    emojis = _get_static_emojis()
    
    if not module_name_input:
        return await build_and_edit(event, [{"text": f"Укажите имя модуля для {action}а.", "entity": MessageEntityBold}, {"text": f"\nИспользование: {prefix}{action} <module>", "entity": MessageEntityCode}])
    
    module_name = _find_module_by_name_with_client(module_name_input, event.client)
    if not module_name:
        return await build_and_edit(event, [
            _build_emoji_part(emojis['ERROR']), 
            {"text": " Ошибка: ", "entity": MessageEntityBold}, 
            {"text": "Модуль "},
            {"text": module_name_input, "entity": MessageEntityCode},
            {"text": " не найден."}
        ])

    # ❗️❗️❗️ ЗАЩИТА ОТ ВЫГРУЗКИ СИСТЕМНЫХ МОДУЛЕЙ ❗️❗️❗️
    if action == "unload" and module_name in PROTECTED_MODULES:
         return await build_and_edit(event, [
             _build_emoji_part(emojis['LOCK']),
             {"text": " Ошибка: ", "entity": MessageEntityBold},
             {"text": "Модуль ", "entity": MessageEntityBold},
             {"text": module_name, "entity": MessageEntityCode},
             {"text": " защищен от выгрузки.", "entity": MessageEntityBold}
         ])
    # -------------------------------------------------------

    # Если модуль уже загружен и мы вызываем .load - делаем reload
    _loaded_mods = getattr(event.client, 'modules', {})
    _mn_lower = module_name.lower()
    _already_loaded = (module_name in _loaded_mods
                       or any(_k.lower().replace("heroku:", "") == _mn_lower
                              or _k.lower().replace("heroku:", "").startswith(_mn_lower)
                              for _k in _loaded_mods))
    if action == "load" and _already_loaded:
        action = "reload"

    action_map = {
        "load": {"verb": "Загружаю", "emoji": emojis['ROCKET'], "func": load_module},
        "unload": {"verb": "Выгружаю", "emoji": emojis['DB'], "func": unload_module},
        "reload": {"verb": "Перезагружаю", "emoji": emojis['UPDATE'], "func": reload_module},
    }
    op = action_map[action]
    
    await build_and_edit(event, [_build_emoji_part(op["emoji"]), {"text": f" {op['verb']} модуль ", "entity": MessageEntityBold}, {"text": module_name, "entity": MessageEntityCode}, {"text": "...", "entity": MessageEntityBold}])
    
    try:
        if action == "reload": result = await op["func"](event.client, module_name, event.chat_id)
        else: result = await op["func"](event.client, module_name)
        update_state_file(event.client)

        # Heroku loader возвращает {"status":"ok","module_name":...,"commands":[...]}
        # без ключа "message" — строим его сами если отсутствует
        if "message" not in result:
            if result.get("status") == "ok":
                _cmds = result.get("commands", [])
                _mname = result.get("module_name", module_name)
                result["message"] = (f"Модуль {_mname} успешно загружен."
                                     + (f" Команды: {', '.join('.' + c for c in _cmds)}" if _cmds else ""))
            else:
                result["message"] = result.get("status", "Неизвестная ошибка")

        parts = []
        if result["status"] == "ok" or result["status"] == "info":
            parts.append(_build_emoji_part(emojis['SUCCESS']))
            parts.append({"text": f" {result['message']}"})
        else:
            parts.append(_build_emoji_part(emojis['ERROR']))
            parts.append({"text": " Ошибка: ", "entity": MessageEntityBold})
            parts.append({"text": result['message']})
            if "traceback" in result:
                 parts.append({"text": "\n\nLogs:\n", "entity": MessageEntityBold})
                 parts.append({"text": result["traceback"], "entity": MessageEntityBlockquote, "kwargs": {"collapsed": True}})
            else:
                 parts.append({"text": result['message'], "entity": MessageEntityCode})
        await build_and_edit(event, parts, link_preview=False)
    except Exception as e:
        await build_and_edit(event, [_build_emoji_part(emojis['ERROR']), {"text": " Критическая ошибка: ", "entity": MessageEntityBold}, {"text": str(e), "entity": MessageEntityCode}])

@register("load", incoming=True)
async def load_cmd(event):
    """Загружает модуль.
    Usage: {prefix}load <название>"""
    await _handle_module_command(event, "load")

@register("unload", incoming=True)
async def unload_cmd(event):
    """Выгружает модуль.
    Usage: {prefix}unload <название>"""
    await _handle_module_command(event, "unload")

@register("reload", incoming=True)
async def reload_cmd(event):
    """Перезагружает модуль.
    Usage: {prefix}reload <название>"""
    await _handle_module_command(event, "reload")

def get_module_size(module_name):
    real_name = _find_module_by_name(module_name)
    if not real_name: return None
    potential_paths = list(MODULES_DIR.rglob(f"{real_name.replace('.', '/')}.py"))
    if potential_paths:
        path = potential_paths[0]
        if path.exists(): return round(path.stat().st_size / 1024, 2)
    return None

def get_module_commands(module_name, client=None):
    _mn_norm = _normalize(module_name)
    # Собираем метки heroku-модулей у которых file_name совпадает с module_name
    # (напр. module_name="goypulse", метка "heroku:GoyPulse V9")
    _file_name_labels = set()
    if client is not None:
        for _k, _v in getattr(client, 'modules', {}).items():
            if isinstance(_v, dict) and _normalize(_v.get("file_name", "")) == _mn_norm:
                _file_name_labels.add(_k)

    def _matches(mod_label):
        _bare = mod_label.replace("heroku:", "").replace("Heroku:", "")
        return (mod_label == module_name
                or mod_label == f"heroku:{module_name}"
                or _normalize(_bare) == _mn_norm
                or mod_label in _file_name_labels)
    return [cmd for cmd, info_list in COMMANDS_REGISTRY.items()
            if info_list and _matches(info_list[0].get('module', ''))]


@register("cfg", incoming=True)
async def cfg_cmd(event):
    """Управление конфигом модулей (Heroku/Hikka-совместимых).
    Usage: {prefix}cfg <модуль> - показать конфиг
           {prefix}cfg <модуль> <ключ> <значение> - установить значение
           {prefix}cfg <модуль> <ключ> - посмотреть одно значение"""
    if not check_permission(event, min_level="TRUSTED"):
        return
    emojis = _get_static_emojis()
    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=3)

    if len(args) < 2:
        return await build_and_edit(event, [
            _build_emoji_part(emojis['INFO']),
            {"text": " Использование:\n", "entity": MessageEntityBold},
            {"text": f"{prefix}cfg <модуль>", "entity": MessageEntityCode},
            {"text": " — показать все настройки\n"},
            {"text": f"{prefix}cfg <модуль> <ключ> <значение>", "entity": MessageEntityCode},
            {"text": " — установить значение\n"},
        ])

    module_name_input = args[1].strip()
    module_name = _find_module_by_name_with_client(module_name_input, event.client)
    if not module_name:
        return await build_and_edit(event, [
            _build_emoji_part(emojis['ERROR']),
            {"text": " Модуль "},
            {"text": module_name_input, "entity": MessageEntityCode},
            {"text": " не найден.", "entity": MessageEntityBold},
        ])

    # Получаем живой инстанс модуля чтобы достать _meta из ModuleConfig.
    # client.modules хранит ключи как "heroku:ChatGPT" (имя класса) или "chatgpt" (файл).
    # Ищем без учёта регистра и с/без префикса heroku:.
    _mods = getattr(event.client, 'modules', {})
    mod_instance = None
    _search = module_name.lower()
    for _key, _val in _mods.items():
        _bare = _key.lower().replace("heroku:", "")
        if _bare == _search:
            # _val — словарь {"instance": ..., ...} для heroku или сам объект
            if isinstance(_val, dict):
                mod_instance = _val.get("instance")
            else:
                mod_instance = _val
            break

    def _get_meta():
        """Возвращает _meta из ModuleConfig инстанса если есть."""
        if mod_instance and hasattr(mod_instance, 'config'):
            cfg = mod_instance.config
            if hasattr(cfg, '_meta'):
                return cfg._meta
        return {}

    # --- Показать все настройки ---
    if len(args) == 2:
        saved = db.get_all_module_configs(module_name)
        meta = _get_meta()

        if not meta and not saved:
            return await build_and_edit(event, [
                _build_emoji_part(emojis['INFO']),
                {"text": f" У модуля "},
                {"text": module_name, "entity": MessageEntityCode},
                {"text": " нет конфигурируемых параметров."},
            ])

        parts = [
            _build_emoji_part(emojis['WRENCH']),
            {"text": f" Конфиг модуля ", "entity": MessageEntityBold},
            {"text": module_name, "entity": MessageEntityCode},
            {"text": "\n\n"},
        ]

        # Показываем параметры из _meta (с описанием) + из БД
        all_keys = set(meta.keys()) | set(saved.keys())
        for key in sorted(all_keys):
            current_val = saved.get(key)
            if current_val is None and key in meta:
                current_val = meta[key].default
            doc = meta[key].doc if key in meta else ""
            # Скрываем значение если валидатор Hidden
            is_hidden = False
            if key in meta and meta[key].validator is not None:
                is_hidden = type(meta[key].validator).__name__ == "Hidden"
            display_val = "***" if (is_hidden and current_val) else repr(current_val)
            parts += [
                {"text": f"• "},
                {"text": key, "entity": MessageEntityCode},
                {"text": f" = "},
                {"text": display_val + "\n", "entity": MessageEntityCode},
            ]
            if doc:
                parts.append({"text": f"  {doc}\n", "entity": MessageEntityItalic})

        parts += [
            {"text": "\n"},
            _build_emoji_part(emojis['INFO']),
            {"text": " Изменить: ", "entity": MessageEntityBold},
            {"text": f"{prefix}cfg {module_name} <ключ> <значение>", "entity": MessageEntityCode},
        ]
        return await build_and_edit(event, parts)

    # --- Установить значение ---
    if len(args) >= 4:
        key = args[2].strip()
        value = args[3].strip()

        # Ищем ключ — сначала в ModuleConfig, потом в module_data (self.set/get)
        meta = _get_meta()
        in_config = key in meta

        if in_config:
            # Сохраняем как конфиг
            db.set_module_config(module_name, key, value)
            if mod_instance and hasattr(mod_instance, 'config'):
                try:
                    mod_instance.config.set_db_value(key, value)
                    mod_instance.config[key] = value
                except Exception:
                    pass
        else:
            # Сохраняем как module_data (self.set/get из Hikka/Heroku модулей)
            # Конвертируем строку в правильный тип
            real_value = value
            if value.lower() in ("true", "false"):
                real_value = value.lower() == "true"
            else:
                try: real_value = int(value)
                except ValueError:
                    try: real_value = float(value)
                    except ValueError: pass
            db.set_module_data(module_name, key, real_value)
            # Обновляем живой инстанс если есть db адаптер
            if mod_instance and hasattr(mod_instance, 'db') and mod_instance.db:
                try:
                    mod_instance.db.set(module_name, key, real_value)
                except Exception:
                    pass

        return await build_and_edit(event, [
            _build_emoji_part(emojis['SUCCESS']),
            {"text": " Обновлено: ", "entity": MessageEntityBold},
            {"text": f"{module_name}", "entity": MessageEntityCode},
            {"text": " → "},
            {"text": key, "entity": MessageEntityCode},
            {"text": " = "},
            {"text": value, "entity": MessageEntityCode},
            {"text": f"\n({'config' if in_config else 'data'})"},
        ])

    # --- Показать одно значение ---
    if len(args) == 3:
        key = args[2].strip()
        val = db.get_module_config(module_name, key)
        meta = _get_meta()
        if val is None and key in meta:
            val = meta[key].default
        if val is None:
            return await build_and_edit(event, [
                _build_emoji_part(emojis['ERROR']),
                {"text": f" Ключ "},
                {"text": key, "entity": MessageEntityCode},
                {"text": " не найден в конфиге ", "entity": MessageEntityBold},
                {"text": module_name, "entity": MessageEntityCode},
            ])
        is_hidden = False
        if key in meta and meta[key].validator is not None:
            is_hidden = type(meta[key].validator).__name__ == "Hidden"
        display_val = "***" if (is_hidden and val) else repr(val)
        return await build_and_edit(event, [
            _build_emoji_part(emojis['INFO']),
            {"text": f" {module_name} → ", "entity": MessageEntityBold},
            {"text": key, "entity": MessageEntityCode},
            {"text": " = "},
            {"text": display_val, "entity": MessageEntityCode},
        ])