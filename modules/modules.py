# modules/modules.py
"""Управление модулями: загрузка, выгрузка, перезагрузка и просмотр информации.

<manifest>
version: 1.0.0
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/modules.py
author: Kote

Команды:
• modules [поиск] - Показать список модулей
• minfo <название> - Информация о модуле
• load <название> - Загрузить модуль
• unload <название> - Выгрузить модуль
• reload <название> - Перезагрузить модуль
• setmodemoji <ключ> <эмодзи> | <fallback> - Установить эмодзи
• delmodemoji <ключ> - Сбросить эмодзи
• modemojis - Показать эмодзи
</manifest>"""

import os
import shutil
from datetime import datetime
from pathlib import Path
from core import register
from utils import database as db
from services.module_info_cache import parse_manifest
from utils.loader import get_all_modules, COMMANDS_REGISTRY, load_module, unload_module, reload_module
from services.state_manager import update_state_file
from utils.message_builder import build_and_edit, build_message
from utils.security import check_permission
from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityBold, MessageEntityCode, MessageEntityItalic
from telethon.errors.rpcerrorlist import MessageNotModifiedError

MODULES_DIR = Path(__file__).parent.parent / "modules"
BACKUPS_DIR = Path(__file__).parent.parent / "backups"
SYSTEM_MODULE_NAMES = ["admin", "help", "fun", "install", "modules", "updater", "logs", "ping", "exec", "profile", "config", "git_manager"]

def _get_static_emojis() -> dict:
    """Загружает кастомные СТАТИЧНЫЕ эмодзи для modules.py из БД."""
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
    }
    custom_emojis = db.get_module_data("modules", "modules_emojis", default={})
    return {**DEFAULT_STATIC_EMOJIS, **custom_emojis}

def _build_emoji_part(emoji_details: dict) -> dict:
    """
    Умный сборщик. Всегда возвращает fallback и накладывает ID, если он есть.
    """
    part = {"text": emoji_details.get('fallback', '❔')}
    if emoji_details.get('id') != 0:
        part["entity"] = MessageEntityCustomEmoji
        part["kwargs"] = {"document_id": emoji_details['id']}
    return part

async def _parse_emoji_args(event, cmd_name: str, example_key: str) -> dict:
    """Парсер аргументов для команд .setmodemoji"""
    prefix = db.get_setting('prefix', '.')
    args_str = event.pattern_match.group(1)
    fallback_char = "❔"
    args_before_pipe = args_str
    
    if "|" in (args_str or ""):
        parts = args_str.split("|", 1)
        args_before_pipe = parts[0].strip()
        fallback_text = parts[1].strip()
        if fallback_text:
            fallback_char = fallback_text[0]
    
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
            if isinstance(entity, MessageEntityCustomEmoji) and entity.offset >= (len(prefix) + len(cmd_name) + len(key)):
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
        try:
            emoji_id = int(parts[1])
        except (ValueError, TypeError):
            return {"error": [{"text": "❌ ID должен быть числом"}]}
    else:
        return {"error": [{"text": "❌ Укажите ID или Премиум-Эмодзи"}]}
    
    if fallback_char == "❔" and emoji_id != 0:
         return {"error": [{"text": "❌ Укажите fallback-символ после |"}]}
            
    return {"key": key, "id": emoji_id, "fallback": fallback_char}

@register("setmodemoji", incoming=True)
async def setmodemoji_cmd(event):
    """Устанавливает кастомный статичный эмодзи для модуля modules."""
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [
            {"text": "🚫 Доступ запрещен.", "entity": MessageEntityBold}
        ])
        
    parsed = await _parse_emoji_args(event, "setmodemoji", "PACKAGE")
    if "error" in parsed:
        return await build_and_edit(event, parsed["error"])
    key_upper = parsed["key"].upper()
    if key_upper not in _get_static_emojis():
        return await build_and_edit(event, [{"text": "❌ Неизвестный ключ" }])
    custom_emojis = db.get_module_data("modules", "modules_emojis", default={})
    custom_emojis[key_upper] = {"id": parsed["id"], "fallback": parsed["fallback"]}
    db.set_module_data("modules", "modules_emojis", custom_emojis)
    await build_and_edit(event, [
        {"text": "✅ "}, 
        {"text": f"Эмодзи для {key_upper} (в modules.py) установлен!", "entity": MessageEntityBold}
    ])

@register("delmodemoji", incoming=True)
async def delmodemoji_cmd(event):
    """Сбрасывает статичный эмодзи для модуля modules."""
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [
            {"text": "🚫 Доступ запрещен.", "entity": MessageEntityBold}
        ])
        
    key_upper = (event.pattern_match.group(1) or "").upper()
    if not key_upper:
        return await build_and_edit(event, [{"text": "❌ Укажите ключ"}])
    custom_emojis = db.get_module_data("modules", "modules_emojis", default={})
    if key_upper in custom_emojis:
        del custom_emojis[key_upper]
        db.set_module_data("modules", "modules_emojis", custom_emojis)
        await build_and_edit(event, [{"text": "🗑️ Эмодзи сброшены."}])
    else:
        await build_and_edit(event, [{"text": "ℹ️ Эмодзи не был найден."}])

@register("modemojis", incoming=True)
async def modemojis_cmd(event):
    """Показывает текущие настройки статичных эмодзи для modules.py."""
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [
            {"text": "🚫 Доступ запрещен.", "entity": MessageEntityBold}
        ])
        
    parts = [
        {"text": "⚙️ "}, 
        {"text": "Эмодзи для `modules.py`", "entity": MessageEntityBold}, 
        {"text": "\n(Кастомные из БД перезаписывают дефолтные)\n\n"}
    ]
    mapping = _get_static_emojis()
    custom_keys = db.get_module_data("modules", "modules_emojis", default={}).keys()
    for key, details in sorted(mapping.items()):
        is_custom = " (кастомный)" if key in custom_keys else ""
        parts.append(_build_emoji_part(details))
        parts.append({"text": f" {key}{is_custom}: ", "entity": MessageEntityBold})
        if details['id'] != 0:
            parts.append({"text": str(details['id']), "entity": MessageEntityCode})
        else:
            parts.append({"text": "ID не задан", "entity": MessageEntityItalic})
        parts.append({"text": "\n"})
    await build_and_edit(event, parts)

@register("modules", incoming=True)
async def list_modules(event):
    """Показывает детальный список всех модулей."""
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [
            {"text": "🚫 Доступ запрещен.", "entity": MessageEntityBold}
        ])
        
    emojis = _get_static_emojis()
    prefix = db.get_setting("prefix", default=".")
    
    args = event.message.text.split(maxsplit=1)
    search_query = args[1].lower() if len(args) > 1 else None
    all_modules = get_all_modules()
    loaded_modules = set(event.client.modules.keys() if hasattr(event.client, 'modules') else [])
    
    if search_query:
        all_modules = [mod for mod in all_modules if search_query in mod.lower()]
    
    if not all_modules:
        query_text = f" по запросу '{search_query}'" if search_query else ""
        return await build_and_edit(event, [
            _build_emoji_part(emojis['PACKAGE']),
            {"text": f"Модули{query_text} не найдены.", "entity": MessageEntityBold}
        ])
    
    system_modules, user_modules = [], []
    for module in sorted(all_modules):
        info = {'name': module, 'loaded': module in loaded_modules, 'commands': get_module_commands(module), 'size': get_module_size(module)}
        (system_modules if module.lower() in SYSTEM_MODULE_NAMES else user_modules).append(info)
    
    parts = [
        _build_emoji_part(emojis['PACKAGE']),
        {"text": "Управление модулями", "entity": MessageEntityBold},
        {"text": "\n\n"}
    ]
    
    if search_query:
        parts.extend([
            _build_emoji_part(emojis['SEARCH']),
            {"text": f" Результаты поиска: "},
            {"text": f"{search_query}", "entity": MessageEntityCode},
            {"text": "\n\n"}
        ])
    
    def format_section(modules_list, title, emoji_details):
        if not modules_list: return
        parts.append(_build_emoji_part(emoji_details))
        parts.extend([
            {"text": f" {title}", "entity": MessageEntityBold},
            {"text": f" ({len(modules_list)}):\n"}
        ])
        for mod in modules_list:
            status_emoji = "✅" if mod['loaded'] else "❌"
            cmd_count, size_kb = len(mod['commands']), mod['size']
            parts.append({"text": f"{status_emoji} "})
            parts.append({"text": f"{mod['name']}", "entity": MessageEntityCode})
            if cmd_count > 0: parts.append({"text": f" • {cmd_count} cmd"})
            if size_kb: parts.append({"text": f" • {size_kb} KB"})
            parts.append({"text": "\n"})
        parts.append({"text": "\n"})
    
    format_section(system_modules, "Системные модули", emojis['SETTINGS'])
    format_section(user_modules, "Пользовательские модули", emojis['WRENCH'])
    
    total_commands = sum(len(get_module_commands(m)) for m in all_modules)
    parts.extend([
        _build_emoji_part(emojis['CHART']),
        {"text": " Статистика:", "entity": MessageEntityBold},
        {"text": "\n"},
        {"text": f"• Всего модулей: {len(all_modules)}\n"},
        {"text": f"• Загружено: {len(loaded_modules)}/{len(all_modules)}\n"},
        {"text": f"• Команд доступно: {total_commands}"}
    ])
    
    await build_and_edit(event, parts)

@register("minfo", incoming=True)
async def module_info(event):
    """Показывает подробную информацию о модуле."""
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [
            {"text": "🚫 Доступ запрещен.", "entity": MessageEntityBold}
        ])
        
    emojis = _get_static_emojis()
    prefix = db.get_setting("prefix", default=".")
    
    args = event.message.text.split(maxsplit=1)
    if len(args) < 2:
        return await build_and_edit(event, [
            _build_emoji_part(emojis['INFO']),
            {"text": " Укажите имя модуля:\n", "entity": MessageEntityBold},
            {"text": f"{prefix}minfo <module_name>", "entity": MessageEntityCode}
        ])
    
    module_name = args[1].strip()
    
    module_path = None
    potential_paths = list(MODULES_DIR.rglob(f"{module_name.replace('.', '/')}.py"))
    if potential_paths:
        module_path = potential_paths[0]
    
    if not module_path or not module_path.exists():
        return await build_and_edit(event, [
            _build_emoji_part(emojis['ERROR']),
            {"text": f" Модуль `{module_name}` не найден.", "entity": MessageEntityBold}
        ])
    
    manifest = parse_manifest(module_path.read_text(encoding='utf-8'))
    
    parts = [
        _build_emoji_part(emojis['INFO']),
        {"text": " Информация о модуле ", "entity": MessageEntityBold},
        {"text": module_name, "entity": MessageEntityCode},
        {"text": "\n\n"}
    ]
    
    if manifest["description"]:
        parts.append(_build_emoji_part(emojis['DESC']))
        parts.extend([
            {"text": " Описание:\n", "entity": MessageEntityBold},
            {"text": manifest["description"], "entity": MessageEntityItalic},
            {"text": "\n\n"}
        ])
    
    parts.extend([
        _build_emoji_part(emojis['VERSION']),
        {"text": " Версия: ", "entity": MessageEntityBold},
        {"text": f"{manifest.get('version', 'N/A')}\n"},
        
        _build_emoji_part(emojis['SOURCE']),
        {"text": " Источник: ", "entity": MessageEntityBold},
        {"text": f"{manifest.get('source', 'N/A')}\n"},
        
        _build_emoji_part(emojis['AUTHOR']),
        {"text": " Автор: ", "entity": MessageEntityBold},
        {"text": f"{manifest.get('author', 'Неизвестно')}\n\n"}
    ])
    
    size_kb = round(module_path.stat().st_size / 1024, 2)
    mtime = datetime.fromtimestamp(module_path.stat().st_mtime)
    loaded = module_name in getattr(event.client, 'modules', {})
    
    parts.extend([
        _build_emoji_part(emojis['CHART']),
        {"text": f" Размер: {size_kb} KB\n"},
        _build_emoji_part(emojis['CALENDAR']),
        {"text": f" Изменен: {mtime.strftime('%d.%m.%Y %H:%M')}\n"},
        _build_emoji_part(emojis['UPDATE']),
        {"text": " Статус: ", "entity": MessageEntityBold},
        (_build_emoji_part(emojis['SUCCESS']) if loaded else _build_emoji_part(emojis['ERROR'])),
        {"text": " Загружен\n\n" if loaded else " Не загружен\n\n"}
    ])
    
    commands = get_module_commands(module_name)
    if commands:
        parts.extend([
            _build_emoji_part(emojis['WRENCH']),
            {"text": f" Команды ({len(commands)}):\n", "entity": MessageEntityBold}
        ])
        for cmd in sorted(commands):
            doc = COMMANDS_REGISTRY.get(cmd, [{}])[0].get('doc', '')
            short_desc = doc.split('\n')[0][:50]
            parts.extend([
                {"text": "• "},
                {"text": f"{prefix}{cmd}", "entity": MessageEntityCode},
                {"text": f" - {short_desc}\n"}
            ])
        parts.append({"text": "\n"})
    
    db_configs = db.get_all_module_configs(module_name)
    db_data = db.get_all_module_data(module_name)
    if db_configs or db_data:
        parts.extend([
            _build_emoji_part(emojis['DB']),
            {"text": " Данные в БД:\n", "entity": MessageEntityBold}
        ])
        if db_configs: 
            parts.append({"text": f"• Настроек: {len(db_configs)}\n"})
        if db_data: 
            parts.append({"text": f"• Записей данных: {len(db_data)}\n"})
    
    await build_and_edit(event, parts, link_preview=False)

@register("load", incoming=True)
async def load_cmd(event):
    """Загружает указанный модуль."""
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 Доступ запрещен."}])
        
    prefix = db.get_setting("prefix", default=".")
    module_name = event.pattern_match.group(1)
    
    if not module_name:
        return await build_and_edit(event, [
            {"text": "<b>Укажите имя модуля для загрузки.</b>\n"},
            {"text": f"Использование: {prefix}load <module>", "entity": MessageEntityCode}
        ])
    
    await build_and_edit(event, [{"text": f"⏳ <b>Загружаю модуль <code>{module_name}</code>...</b>"}])
    result = await load_module(event.client, module_name, event.chat_id)
    update_state_file(event.client)
    if result:
        await build_and_edit(event, [{"text": result}])

@register("unload", incoming=True)
async def unload_cmd(event):
    """Выгружает указанный модуль."""
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 Доступ запрещен."}])
        
    prefix = db.get_setting("prefix", default=".")
    module_name = event.pattern_match.group(1)
    
    if not module_name:
        return await build_and_edit(event, [
            {"text": "<b>Укажите имя модуля для выгрузки.</b>\n"},
            {"text": f"Использование: {prefix}unload <module>", "entity": MessageEntityCode}
        ])

    await build_and_edit(event, [{"text": f"🗑️ <b>Выгружаю модуль <code>{module_name}</code>...</b>"}])
    result = await unload_module(event.client, module_name)
    update_state_file(event.client)
    await build_and_edit(event, [{"text": result}])

@register("reload", incoming=True)
async def reload_cmd(event):
    """Перезагружает указанный модуль."""
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 Доступ запрещен."}])
        
    prefix = db.get_setting("prefix", default=".")
    module_name = event.pattern_match.group(1)
    
    if not module_name:
        return await build_and_edit(event, [
            {"text": "<b>Укажите имя модуля для перезагрузки.</b>\n"},
            {"text": f"Использование: {prefix}reload <module>", "entity": MessageEntityCode}
        ])

    await build_and_edit(event, [{"text": f"♻️ <b>Перезагружаю модуль <code>{module_name}</code>...</b>"}])
    result = await reload_module(event.client, module_name, event.chat_id)
    update_state_file(event.client)
    if result:
        await build_and_edit(event, [{"text": result}])

def get_module_size(module_name):
    potential_paths = list(MODULES_DIR.rglob(f"{module_name.replace('.', '/')}.py"))
    if potential_paths:
        path = potential_paths[0]
        if path.exists(): return round(path.stat().st_size / 1024, 2)
    return None

def get_module_commands(module_name):
    return [cmd for cmd, info_list in COMMANDS_REGISTRY.items() if info_list[0]['module'] == module_name]