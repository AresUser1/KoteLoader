# modules/profile.py
"""
Показывает подробную информационную карточку о юзерботе.
Этот модуль позволяет гибко настраивать отображаемую информацию.

<manifest>
Команды:
• info - Отобразить настроенную инфо-карточку.
• setbio <текст> - Установить/изменить свое био (с форматированием).
• addfield <название> | <значение> - Добавить кастомное поле (с форматированием).
• delfield <название> - Удалить кастомное поле.
• setpfp - (в ответ на медиа) Установить медиа для профиля.
• delpfp - Удалить медиа из профиля.
• setpemoji <ключ> <ЭМОДЗИ или ID> | <fallback> - Установить статичный эмодзи (ключи: PAW_1, OWNER, CPU, RAM и т.д.).
• delpemoji <ключ> - Сбросить статичный эмоdзи.
• pemojis - Список всех статичных эмодзи.
• setosemoji <OS> <ЭМОДЗИ или ID> | <fallback> - Установить эмодзи для ОС.
• delosemoji <OS> - Сбросить эмодзи для ОС.
• osemojis - Список всех эмодзи для ОС.
• resetemojis - СБРОСИТЬ ВСЕ ЭМОДЗИ .info до заводских настроек.
• setinfo <текст> - Установить полностью кастомный текст для .info.
• delinfo - Сбросить кастомное .info до стандартной карточки.
• infovars - Показать список переменных для .setinfo.
</manifest>
"""

import time
import platform
import json
import os
import re
from datetime import timedelta
import psutil
import git
from core import register
from utils import database as db
from main import START_TIME
from utils.message_builder import build_message, build_and_edit
# ❗️ ДОБАВЛЕН ИМПОРТ
from utils.security import check_permission
from telethon.tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCode, MessageEntityPre,
    MessageEntityUnderline, MessageEntityStrike, MessageEntityCustomEmoji,
    MessageEntityTextUrl, MessageEntityBlockquote
)
from telethon.errors.rpcerrorlist import MessageNotModifiedError, DocumentInvalidError, MessageIdInvalidError

# --- Вспомогательные функции ---

def get_uptime() -> str:
    """Возвращает время работы бота в читаемом формате."""
    return str(timedelta(seconds=int(time.time() - START_TIME)))

def get_git_info() -> dict:
    """Возвращает информацию из Git."""
    try:
        repo = git.Repo(search_parent_directories=True)
        branch = repo.active_branch.name
        commit = repo.head.commit
        commit_sha = commit.hexsha[:7]
        repo_url = db.get_setting("repo_url")
        commit_url = f"{repo_url}/commit/{commit.hexsha}" if repo_url else None
        
        repo.remotes.origin.fetch()
        status = "Актуальная версия" if repo.head.commit == repo.remotes.origin.refs[branch].commit else "Доступно обновление!"
        return {"branch": branch, "commit_sha": commit_sha, "commit_url": commit_url, "status": status}
    except Exception:
        return {"branch": "N/A", "commit_sha": "N/A", "commit_url": None, "status": "N/A"}

# --- ❗️ ИСПРАВЛЕНА СИСТЕМА ЭМОДЗИ ---

def _get_static_emojis() -> dict:
    """Загружает кастомные СТАТИЧНЫЕ эмодзи (PAW_1, OWNER, CPU и т.д.) из БД."""
    # ❗️ ВСЕ ID СБРОШЕНЫ НА 0, ЧТОБЫ НЕ БЫЛО ОШИБОК
    DEFAULT_STATIC_EMOJIS = {
        "PAW_1":    {"id": 0, "fallback": "🐾"},
        "PAW_2":    {"id": 0, "fallback": "🐾"},
        "PAW_3":    {"id": 0, "fallback": "🐾"},
        "OWNER":    {"id": 0, "fallback": "😎"},
        "BIO":      {"id": 0, "fallback": "💬"},
        "VERSION":  {"id": 0, "fallback": "💫"},
        "BRANCH":   {"id": 0, "fallback": "🌳"},
        "STATUS":   {"id": 0, "fallback": "😌"},
        "PREFIX":   {"id": 0, "fallback": "⌨️"},
        "UPTIME":   {"id": 0, "fallback": "⌛️"},
        "CPU":      {"id": 0, "fallback": "⚡️"},
        "RAM":      {"id": 0, "fallback": "💼"},
    }
    custom_emojis = db.get_module_data("profile", "static_emojis", default={})
    return {**DEFAULT_STATIC_EMOJIS, **custom_emojis}

def _get_os_emoji_mapping() -> dict:
    """Загружает кастомные ДИНАМИЧЕСКИЕ эмодзи (для ОС) из БД."""
    # ❗️ ВСЕ ID СБРОШЕНЫ НА 0
    DEFAULT_OS_EMOJIS = {
        "Linux":    {"id": 0, "fallback": "🐧"},
        "Ubuntu":   {"id": 0, "fallback": "🟠"},
        "Mint":     {"id": 0, "fallback": "🟢"},
        "Termux":   {"id": 0, "fallback": "⚫️"},
        "JamHost":  {"id": 0, "fallback": "🍓"},
        "Arch":     {"id": 0, "fallback": "🔵"},
        "Debian":   {"id": 0, "fallback": "🔴"},
        "Fedora":   {"id": 0, "fallback": "🔵"},
        "Windows":  {"id": 0, "fallback": "🪟"},
        "macOS":    {"id": 0, "fallback": "🍏"},
        "Other":    {"id": 0, "fallback": "💻"}
    }
    custom_emojis = db.get_module_data("profile", "os_emojis", default={})
    return {**DEFAULT_OS_EMOJIS, **custom_emojis}

def _build_emoji_part(emoji_details: dict, force_fallback: bool = False) -> dict:
    """
    Умный сборщик. Всегда возвращает fallback и накладывает ID, если он есть.
    force_fallback=True используется для рендера "безопасного режима".
    """
    part = {"text": emoji_details.get('fallback', '❔')}
    if emoji_details.get('id') != 0 and not force_fallback:
        part["entity"] = MessageEntityCustomEmoji
        part["kwargs"] = {"document_id": emoji_details['id']}
    return part

def get_system_info() -> dict:
    """Возвращает информацию о системе, включая детальное имя OS и эмодзи."""
    process = psutil.Process(os.getpid())
    cpu_usage = process.cpu_percent()
    ram_usage = process.memory_info().rss / (1024 * 1024)
    os_name = "Other"
    
    if os.environ.get("TERMUX_VERSION"):
        os_name = "Termux"
    else:
        system = platform.system()
        if system == "Linux":
            os_name = "Linux"
            hostname = platform.node().lower()
            if "jam" in hostname: os_name = "JamHost"
            else:
                try:
                    release_info = platform.platform().lower()
                    if "ubuntu" in release_info: os_name = "Ubuntu"
                    elif "mint" in release_info: os_name = "Mint"
                    elif "arch" in release_info: os_name = "Arch"
                    elif "debian" in release_info: os_name = "Debian"
                    elif "fedora" in release_info: os_name = "Fedora"
                except Exception: pass 
        elif system == "Windows": os_name = "Windows"
        elif system == "Darwin": os_name = "macOS"
        else: os_name = system if system else "Other"

    os_emoji_mapping = _get_os_emoji_mapping()
    os_emoji_details = os_emoji_mapping.get(os_name, os_emoji_mapping["Other"])
    return {"cpu": cpu_usage, "ram": ram_usage, "os_name": os_name, "os_emoji": os_emoji_details}

# --- Команды управления профилем (Bio, Fields) ---

@register("setbio", incoming=True)
async def setbio_cmd(event):
    """Устанавливает кастомное био (с поддержкой форматирования)."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    prefix = db.get_setting('prefix', '.')
    if not event.pattern_match.group(1):
        return await build_and_edit(event, [
            {"text": "❌ ... Вы не указали текст ..."},
            {"text": f"\nПример: {prefix}setbio ...", "entity": MessageEntityCode},
        ]) # (сокращено)

    match = event.pattern_match
    text_content = match.group(1)
    content_offset = match.start(1) 
    entities_list = []
    if event.entities:
        for e in event.entities:
            if e.offset >= content_offset:
                e.offset -= content_offset 
                entities_list.append(e.to_dict()) 

    bio_data = {"text": text_content, "entities": entities_list}
    db.set_module_data("profile", "bio_data_v2", bio_data) 
    await build_and_edit(event, [{"text": "✅ "}, {"text": "Био (с форматированием) обновлено!", "entity": MessageEntityBold}])

@register("addfield", incoming=True)
async def addfield_cmd(event):
    """Добавляет кастомное поле (с поддержкой форматирования в значении)."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    args = event.pattern_match.group(1)
    prefix = db.get_setting('prefix', '.')
    if not args or "|" not in args:
        return await build_and_edit(event, [
             {"text": "❌ ... Неверный формат ..."},
        ]) # (сокращено)
    
    try:
        split_pos = args.find("|")
        name, value_raw = args[:split_pos].strip(), args[split_pos+1:].strip()
    except Exception:
         return await build_and_edit(event, [{"text": "❌ ... Ошибка разбора ..."}])
    if not name or not value_raw:
        return await build_and_edit(event, [{"text": "❌ ... Название и значение не могут быть пустыми ..."}])

    match = event.pattern_match
    try:
        value_start_in_args = args.find(value_raw)
        content_offset = match.start(1) + value_start_in_args
    except Exception:
        content_offset = -1 

    entities_list = []
    if event.entities and content_offset != -1:
        for e in event.entities:
            if e.offset >= content_offset:
                e.offset -= content_offset
                entities_list.append(e.to_dict())

    fields = db.get_module_data("profile", "fields_data_v2", default={})
    fields[name] = {"text": value_raw, "entities": entities_list}
    db.set_module_data("profile", "fields_data_v2", fields)
    await build_and_edit(event, [{"text": "✅ ... Поле ... добавлено ..."}]) # (сокращено)

@register("delfield", incoming=True)
async def delfield_cmd(event):
    """Удаляет кастомное поле из профиля."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    name = event.pattern_match.group(1)
    if not name:
        return await build_and_edit(event, [{"text": "❌ ... Укажите название ..."}])
    fields = db.get_module_data("profile", "fields_data_v2", default={})
    if name in fields:
        del fields[name]
        db.set_module_data("profile", "fields_data_v2", fields)
        await build_and_edit(event, [{"text": "🗑️ ... Поле ... удалено."}])
    else:
        await build_and_edit(event, [{"text": "ℹ️ ... Поле ... не найдено."}])

@register("setpfp", incoming=True)
async def setpfp_cmd(event):
    """Устанавливает медиа для профиля."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    reply = await event.get_reply_message()
    if not reply or not reply.media:
        return await build_and_edit(event, [{"text": "❌ "}, {"text": "Ответьте на фото или видео.", "entity": MessageEntityBold}])
    pointer = {"chat_id": reply.chat_id, "message_id": reply.id}
    db.set_setting("profile_media", json.dumps(pointer))
    await build_and_edit(event, [{"text": "✅ "}, {"text": "Медиа для профиля установлено!", "entity": MessageEntityBold}])

@register("delpfp", incoming=True)
async def delpfp_cmd(event):
    """Удаляет медиа из профиля."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    db.set_setting("profile_media", "")
    await build_and_edit(event, [{"text": "🗑️ "}, {"text": "Медиа для профиля удалено.", "entity": MessageEntityBold}])

# --- Команды управления эмодзи ---

async def _parse_emoji_args(event, cmd_name: str, example_key: str) -> dict:
    """
    Умный парсер. Ищет ID в тексте ИЛИ в entity (вставленный премиум-эмодзи).
    """
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
            {"text": "❌ ... Неверный формат ..."},
            {"text": f"\nПример: {prefix}{cmd_name} {example_key} ...", "entity": MessageEntityCode}
        ]} # (сокращено)

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
            return {"error": [{"text": "❌ ... ID должен быть числом ..."}]}
    else:
        return {"error": [{"text": "❌ ... Укажите ID или Премиум-Эмодзи ..."}]}
    
    if fallback_char == "❔" and emoji_id != 0:
         return {"error": [{"text": "❌ ... Укажите fallback-символ ..."}]}
            
    return {"key": key, "id": emoji_id, "fallback": fallback_char}

@register("setpemoji", incoming=True)
async def setpemoji_cmd(event):
    """Устанавливает кастомный статичный эмодзи (OWNER, CPU, PAW_1 и т.д.)."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    parsed = await _parse_emoji_args(event, "setpemoji", "OWNER")
    if "error" in parsed:
        return await build_and_edit(event, parsed["error"])
    key_upper = parsed["key"].upper()
    if key_upper not in _get_static_emojis():
        return await build_and_edit(event, [{"text": "❌ ... Неизвестный ключ ..."}])
    custom_emojis = db.get_module_data("profile", "static_emojis", default={})
    custom_emojis[key_upper] = {"id": parsed["id"], "fallback": parsed["fallback"]}
    db.set_module_data("profile", "static_emojis", custom_emojis)
    await build_and_edit(event, [{"text": "✅ "}, {"text": f"Эмодзи для {key_upper} установлен!", "entity": MessageEntityBold}])

@register("delpemoji", incoming=True)
async def delpemoji_cmd(event):
    """Сбрасывает статичный эмодзи до дефолтного."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    key_upper = (event.pattern_match.group(1) or "").upper()
    if not key_upper:
        return await build_and_edit(event, [{"text": "❌ ... Укажите ключ ..."}])
    custom_emojis = db.get_module_data("profile", "static_emojis", default={})
    if key_upper in custom_emojis:
        del custom_emojis[key_upper]
        db.set_module_data("profile", "static_emojis", custom_emojis)
        await build_and_edit(event, [{"text": "🗑️ ... сброшены."}])
    else:
        await build_and_edit(event, [{"text": "ℹ️ ... не был найден."}])

@register("pemojis", incoming=True)
async def pemojis_cmd(event):
    """Показывает текущие настройки статичных эмодзи."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    parts = [{"text": "⚙️ ..."}, {"text": "\n(Кастомные из БД ...)\n\n"}]
    mapping = _get_static_emojis()
    custom_keys = db.get_module_data("profile", "static_emojis", default={}).keys()
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

@register("setosemoji", incoming=True)
async def setosemoji_cmd(event):
    """Устанавливает кастомный эмодзи для ОС."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    parsed = await _parse_emoji_args(event, "setosemoji", "Ubuntu")
    if "error" in parsed:
        return await build_and_edit(event, parsed["error"])
    os_name_capitalized = parsed["key"].capitalize()
    custom_emojis = db.get_module_data("profile", "os_emojis", default={})
    custom_emojis[os_name_capitalized] = {"id": parsed["id"], "fallback": parsed["fallback"]}
    db.set_module_data("profile", "os_emojis", custom_emojis)
    await build_and_edit(event, [{"text": "✅ "}, {"text": f"Эмодзи для {os_name_capitalized} установлен!", "entity": MessageEntityBold}])

@register("delosemoji", incoming=True)
async def delosemoji_cmd(event):
    """Сбрасывает эмодзи для ОС до дефолтного."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    os_name = (event.pattern_match.group(1) or "").capitalize()
    if not os_name:
        return await build_and_edit(event, [{"text": "❌ ... Укажите название ОС ..."}])
    custom_emojis = db.get_module_data("profile", "os_emojis", default={})
    if os_name in custom_emojis:
        del custom_emojis[os_name]
        db.set_module_data("profile", "os_emojis", custom_emojis)
        await build_and_edit(event, [{"text": "🗑️ ... сброшены."}])
    else:
        await build_and_edit(event, [{"text": "ℹ️ ... не был найден."}])

@register("osemojis", incoming=True)
async def osemojis_cmd(event):
    """Показывает текущие настройки эмодзи для ОС."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    parts = [{"text": "⚙️ ..."}, {"text": "\n(Кастомные из БД ...)\n\n"}]
    mapping = _get_os_emoji_mapping()
    custom_keys = db.get_module_data("profile", "os_emojis", default={}).keys()
    for os_name, details in sorted(mapping.items()):
        is_custom = " (кастомный)" if os_name in custom_keys else ""
        parts.append(_build_emoji_part(details))
        parts.append({"text": f" {os_name}{is_custom}: ", "entity": MessageEntityBold})
        if details['id'] != 0:
            parts.append({"text": str(details['id']), "entity": MessageEntityCode})
        else:
            parts.append({"text": "ID не задан", "entity": MessageEntityItalic})
        parts.append({"text": "\n"})
    await build_and_edit(event, parts)

@register("resetemojis", incoming=True)
async def resetemojis_cmd(event):
    """Сбрасывает ВСЕ настройки .setpemoji и .setosemoji до дефолтных."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    db.set_module_data("profile", "static_emojis", {})
    db.set_module_data("profile", "os_emojis", {})
    await build_and_edit(event, [
        {"text": "🗑️ "}, 
        {"text": "Все кастомные эмодзи для .info сброшены до заводских настроек.", "entity": MessageEntityBold}
    ])


# --- ❗️ ГЛАВНАЯ КОМАНДА (ИСПРАВЛЕНА) ---

async def _build_info_parts(client, force_fallback: bool = False) -> list:
    """Собирает 'parts' list для .info.
    
    Args:
        client: Экземпляр Telethon клиента.
        force_fallback: Если True, рендерит *только* текстовые fallback-эмодзи.
    """
    
    ENTITY_MAP = {
        'MessageEntityBold': MessageEntityBold, 'MessageEntityItalic': MessageEntityItalic,
        'MessageEntityCode': MessageEntityCode, 'MessageEntityTextUrl': MessageEntityTextUrl,
        'MessageEntityCustomEmoji': MessageEntityCustomEmoji, 'MessageEntityBlockquote': MessageEntityBlockquote
    }
    def _reconstruct_entities(entities_list: list) -> list:
        reconstructed = []
        for e_dict in (entities_list or []):
            class_name = e_dict.get('_')
            if class_name in ENTITY_MAP:
                e_dict.pop('_', None)
                if 'document_id' in e_dict:
                    e_dict['document_id'] = int(e_dict['document_id'])
                reconstructed.append(ENTITY_MAP[class_name](**e_dict))
        return reconstructed

    emojis = _get_static_emojis()
    me = await client.get_me()
    owner = db.get_users_by_level("OWNER")[0]
    owner_entity = await client.get_entity(owner)
    git_info = get_git_info()
    sys_info = get_system_info() 

    parts = [
        _build_emoji_part(emojis['PAW_1'], force_fallback),
        _build_emoji_part(emojis['PAW_2'], force_fallback),
        _build_emoji_part(emojis['PAW_3'], force_fallback),
        {"text": "\n\n", "entity": MessageEntityBold},
        _build_emoji_part(emojis['OWNER'], force_fallback),
        {"text": " Владелец: ", "entity": MessageEntityBold},
        {"text": f"{owner_entity.first_name}", "entity": MessageEntityTextUrl, "kwargs": {"url": f"tg://user?id={owner}"}},
        {"text": "\n"},
    ]

    bio_data = db.get_module_data("profile", "bio_data_v2", default=None)
    if bio_data:
        bio_text = bio_data.get("text", "...")
        bio_entities_raw = bio_data.get("entities", [])
        if force_fallback:
             bio_entities_raw = [e for e in bio_entities_raw if e.get("_") != "MessageEntityCustomEmoji"]
        bio_entities = _reconstruct_entities(bio_entities_raw)
        parts.append(_build_emoji_part(emojis['BIO'], force_fallback))
        parts.append({"text": " Био: \n", "entity": MessageEntityBold})
        parts.append({
            "text": bio_text,
            "entities": [MessageEntityBlockquote(offset=0, length=len(bio_text.encode('utf-16-le')) // 2)] + bio_entities
        })
        parts.append({"text": "\n"})
    
    parts.append({"text": "\n"}) 

    fields_data = db.get_module_data("profile", "fields_data_v2", default={})
    if fields_data:
        for name, data in fields_data.items():
            field_text = data.get("text", "...")
            field_entities_raw = data.get("entities", [])
            if force_fallback:
                field_entities_raw = [e for e in field_entities_raw if e.get("_") != "MessageEntityCustomEmoji"]
            field_entities = _reconstruct_entities(field_entities_raw)
            parts.extend([
                {"text": f"{name}: ", "entity": MessageEntityBold},
            ])
            parts.append({
                "text": field_text,
                "entities": [MessageEntityBlockquote(offset=0, length=len(field_text.encode('utf-16-le')) // 2)] + field_entities
            })
            parts.append({"text": "\n"})
        parts.append({"text": "\n"}) 

    # --- Стандартный блок ---
    parts.append(_build_emoji_part(emojis['VERSION'], force_fallback))
    parts.append({"text": " Версия: 1.0.0 ", "entity": MessageEntityBold})
    commit_url = git_info.get("commit_url")
    if commit_url:
        parts.append({"text": f"#{git_info['commit_sha']}", "entity": MessageEntityTextUrl, "kwargs": {"url": commit_url}})
    else:
        parts.append({"text": f"#{git_info['commit_sha']}", "entity": MessageEntityCode})
    parts.append({"text": "\n"})
    
    parts.append(_build_emoji_part(emojis['BRANCH'], force_fallback))
    parts.extend([
        {"text": " Ветка: ", "entity": MessageEntityBold},
        {"text": f"{git_info['branch']}\n"},
    ])
    
    parts.append(_build_emoji_part(emojis['STATUS'], force_fallback))
    parts.extend([
        {"text": f" {git_info['status']}\n\n", "entity": MessageEntityBold},
    ])

    parts.append(_build_emoji_part(emojis['PREFIX'], force_fallback))
    parts.extend([
        {"text": " Префикс: ", "entity": MessageEntityBold},
        {"text": f"«{db.get_setting('prefix', '.')}»\n"},
    ])
    
    parts.append(_build_emoji_part(emojis['UPTIME'], force_fallback))
    parts.extend([
        {"text": " Аптайм: ", "entity": MessageEntityBold},
        {"text": f"{get_uptime()}\n\n"},
    ])

    parts.append(_build_emoji_part(emojis['CPU'], force_fallback))
    parts.extend([
        {"text": " CPU: ", "entity": MessageEntityBold},
        {"text": f"~{sys_info['cpu']:.1f} %\n"},
    ])

    parts.append(_build_emoji_part(emojis['RAM'], force_fallback))
    parts.extend([
        {"text": " RAM: ", "entity": MessageEntityBold},
        {"text": f"~{sys_info['ram']:.2f} MB\n"},
    ])
    
    parts.append(_build_emoji_part(sys_info['os_emoji'], force_fallback)) 
    parts.append({"text": f" {sys_info['os_name']}"})
    
    return parts

@register("setinfo", incoming=True)
async def setinfo_cmd(event):
    """Устанавливает полностью кастомный текст для `.info`.

    Пример использования:
    `.setinfo Привет, это мой кастомный текст!`

    Текст может содержать форматирование. Все entities будут сохранены и отображены
    как есть при выполнении `.info`.
    """
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    prefix = db.get_setting('prefix', '.')
    raw = event.raw_text or event.text or ''
    cmd_prefix = f"{prefix}setinfo"
    text_content = ''
    entities_list = []

    # Если сообщение начинается с команды, пытаемся извлечь весь текст после неё
    if raw.lower().startswith(cmd_prefix.lower()):
        # Обрезаем команду и оставляем всё остальное
        text_content = raw[len(cmd_prefix):].lstrip()
        if text_content:
            # Вычисляем смещение относительно начала исходного сообщения
            content_offset = len(raw) - len(text_content)
            if event.entities:
                for e in event.entities:
                    # Игнорируем сущности внутри команды
                    if e.offset >= content_offset:
                        new_e = e.to_dict()
                        new_e['offset'] = new_e['offset'] - content_offset
                        entities_list.append(new_e)
    # Если текст не найден, пытаемся использовать текст из ответа
    if not text_content:
        reply = await event.get_reply_message()
        if reply and (reply.raw_text or reply.text):
            text_content = reply.raw_text or reply.text
            # Используем entities из ответа без смещения
            if reply.entities:
                entities_list = [e.to_dict() for e in reply.entities]
    # Если всё ещё нет текста — выводим подсказку
    if not text_content:
        return await build_and_edit(event, [
            {"text": "❌ ... Вы не указали текст ..."},
            {"text": f"\nМожно либо указать текст после команды, либо ответить на сообщение с нужным содержимым.", "entity": MessageEntityItalic},
            {"text": f"\nПример: {prefix}setinfo Привет!", "entity": MessageEntityCode},
        ])
    # Сохраняем текст и связанные entities
    info_data = {"text": text_content, "entities": entities_list}
    db.set_module_data("profile", "custom_info_v2", info_data)
    await build_and_edit(event, [
        {"text": "✅ ", "entity": MessageEntityBold},
        {"text": "Кастомное .info установлено!", "entity": MessageEntityBold}
    ])


@register("delinfo", incoming=True)
async def delinfo_cmd(event):
    """Сбрасывает кастомный текст для `.info`.

    После выполнения этой команды профиль будет отображать стандартную информационную карточку.
    """
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    # Удаляем сохранённые данные о кастомном info
    db.set_module_data("profile", "custom_info_v2", None)
    await build_and_edit(event, [
        {"text": "🗑️ ", "entity": MessageEntityBold},
        {"text": "Кастомное .info удалено. Теперь будет показываться стандартная карточка.", "entity": MessageEntityBold}
    ])


@register("infovars", incoming=True)
async def infovars_cmd(event):
    """Показывает список плейсхолдеров для .setinfo."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    # ❗️ ИСПРАВЛЕНИЕ: Получаем префикс из БД
    prefix = db.get_setting("prefix", default=".")
    
    try:
        # Пытаемся взять эмодзи 'PREFIX' (⌨️) из _get_static_emojis
        emojis = _get_static_emojis()
        emoji_part = _build_emoji_part(emojis.get('PREFIX', {"id": 0, "fallback": "⌨️"}))
    except NameError:
        # Если что-то пошло не так (например, функция не найдена), используем fallback
        emoji_part = {"text": "⌨️"}

    parts = [
        emoji_part,
        {"text": " Переменные для ", "entity": MessageEntityBold},
        # ❗️ ИСПРАВЛЕНИЕ: Используем динамический префикс
        {"text": f"{prefix}setinfo", "entity": MessageEntityCode},
        {"text": "\n\n", "entity": MessageEntityBold},
        {"text": "Эти плейсхолдеры будут заменены на актуальные данные, если они есть в тексте, установленном через ", "entity": MessageEntityItalic},
        # ❗️ ИСПРАВЛЕНИЕ: Используем динамический префикс
        {"text": f"{prefix}setinfo", "entity": MessageEntityCode},
        {"text": ":\n\n", "entity": MessageEntityItalic},
        
        {"text": "• ", "entity": MessageEntityBold},
        {"text": "{owner}", "entity": MessageEntityCode},
        {"text": " - Имя владельца\n"},
        
        {"text": "• ", "entity": MessageEntityBold},
        {"text": "{uptime}", "entity": MessageEntityCode},
        {"text": " - Время работы (аптайм)\n"},
        
        {"text": "• ", "entity": MessageEntityBold},
        {"text": "{cpu}", "entity": MessageEntityCode},
        {"text": " - Нагрузка ЦПУ (%%)\n"},

        {"text": "• ", "entity": MessageEntityBold},
        {"text": "{ram}", "entity": MessageEntityCode},
        {"text": " - Потребление ОЗУ (MB)\n"},
        
        {"text": "• ", "entity": MessageEntityBold},
        {"text": "{os}", "entity": MessageEntityCode},
        {"text": " - Название ОС\n"},
    ]
    await build_and_edit(event, parts)


@register("info", incoming=True)
async def profile_cmd(event):
    """Показывает продвинутую информацию о боте.

    Если пользователь установил кастомный текст через `.setinfo`, то он
    отображается в приоритете. В противном случае строится стандартная
    инфо‑карточка с данными о владельце, био, версии, ветке, статусе, префиксе,
    аптайме, загрузке CPU/RAM и операционной системе.
    """
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    # Проверяем наличие кастомного info
    custom_info = db.get_module_data("profile", "custom_info_v2", default=None)
    if custom_info:
        # Реконструируем entities из сохранённого словаря
        ENTITY_MAP = {
            'MessageEntityBold': MessageEntityBold,
            'MessageEntityItalic': MessageEntityItalic,
            'MessageEntityCode': MessageEntityCode,
            'MessageEntityPre': MessageEntityPre,
            'MessageEntityUnderline': MessageEntityUnderline,
            'MessageEntityStrike': MessageEntityStrike,
            'MessageEntityCustomEmoji': MessageEntityCustomEmoji,
            'MessageEntityTextUrl': MessageEntityTextUrl,
            'MessageEntityBlockquote': MessageEntityBlockquote
        }
        def reconstruct_entities(entities_list):
            reconstructed = []
            for e_dict in entities_list or []:
                class_name = e_dict.get('_')
                if class_name in ENTITY_MAP:
                    params = {k: v for k, v in e_dict.items() if k != '_'}
                    if 'document_id' in params:
                        params['document_id'] = int(params['document_id'])
                    reconstructed.append(ENTITY_MAP[class_name](**params))
            return reconstructed
        
        # Подготовка данных для замены плейсхолдеров
        original_text = custom_info.get('text', '') or ''
        owner_id = db.get_users_by_level("OWNER")[0]
        owner_entity = await event.client.get_entity(owner_id)
        sys_info = get_system_info()
        
        replacements = {
            "{owner}": f"{owner_entity.first_name}",
            "{uptime}": get_uptime(),
            "{cpu}": f"{sys_info['cpu']:.1f} %",
            "{ram}": f"{sys_info['ram']:.2f} MB",
            "{os}": sys_info['os_name'],
        }
        
        # Проверяем наличие плейсхолдеров
        contains_placeholder = any(ph in original_text for ph in replacements.keys())
        
        # Реконструируем entities
        entities = reconstruct_entities(custom_info.get('entities') or [])
        
        if contains_placeholder:
            # === МАГИЯ: Подстановка с пересчётом смещений И длин ===
            text = original_text
            
            # Создаём список замен с их позициями
            replacements_positions = []
            for placeholder, value in replacements.items():
                pos = 0
                while True:
                    pos = text.find(placeholder, pos)
                    if pos == -1:
                        break
                    replacements_positions.append((pos, placeholder, value))
                    pos += len(placeholder)
            
            # Сортируем по позиции (слева направо)
            replacements_positions.sort(key=lambda x: x[0])
            
            # Выполняем замены и корректируем entities
            new_text = ""
            last_pos = 0
            cumulative_shift = 0  # Накопленный сдвиг для корректировки offset
            
            for pos, placeholder, value in replacements_positions:
                # Добавляем текст до плейсхолдера
                new_text += text[last_pos:pos]
                
                # Считаем сдвиг в UTF-16 (Telegram использует UTF-16 для offset)
                old_len_utf16 = len(placeholder.encode('utf-16-le')) // 2
                new_len_utf16 = len(value.encode('utf-16-le')) // 2
                shift = new_len_utf16 - old_len_utf16
                
                # ✅ КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: считаем позицию в НОВОМ тексте (уже частично изменённом)
                current_pos_utf16 = len(new_text.encode('utf-16-le')) // 2
                
                # Корректируем entities
                for entity in entities:
                    entity_start = entity.offset
                    entity_end = entity.offset + entity.length
                    
                    # Премиум-эмодзи и ссылки НЕ ДОЛЖНЫ менять length
                    is_fixed_length = isinstance(entity, (MessageEntityCustomEmoji, MessageEntityTextUrl))
                    
                    # Если entity начинается ПОСЛЕ текущей позиции плейсхолдера — сдвигаем offset
                    if entity_start >= current_pos_utf16 + old_len_utf16:
                        entity.offset += shift
                    
                    # Если entity ОХВАТЫВАЕТ текущую позицию — увеличиваем length (кроме эмодзи)
                    elif entity_start <= current_pos_utf16 < entity_end:
                        if not is_fixed_length:
                            entity.length += shift
                
                # Добавляем замену
                new_text += value
                last_pos = pos + len(placeholder)
                cumulative_shift += shift
            
            # Добавляем остаток текста
            new_text += text[last_pos:]
            text = new_text
        else:
            # Если плейсхолдеров нет — просто берём оригинальный текст
            text = original_text
        
        # === ОБРАБОТКА МЕДИА (БАГ #1 FIX) ===
        media_pointer_str = db.get_setting("profile_media")
        media = None
        if media_pointer_str:
            try:
                pointer = json.loads(media_pointer_str)
                message_to_fetch = await event.client.get_messages(pointer["chat_id"], ids=pointer["message_id"])
                if message_to_fetch and message_to_fetch.media:
                    media = message_to_fetch.media
            except Exception:
                db.set_setting("profile_media", "")
        
        # Отправляем с медиа или без
        try:
            if media:
                await event.client.send_file(
                    event.chat_id,
                    media,
                    caption=text,
                    formatting_entities=entities or None,
                    link_preview=False
                )
                await event.delete()
            else:
                await event.edit(text, formatting_entities=entities or None, link_preview=False)
        except Exception as e:
            # Если сломалось — показываем без форматирования
            fallback_text = f"⚠️ Ошибка рендера:\n`{type(e).__name__}`\n\n{text}"
            if media:
                await event.client.send_file(event.chat_id, media, caption=fallback_text, link_preview=False)
                try:
                    await event.delete()
                except:
                    pass
            else:
                await event.edit(fallback_text, link_preview=False)
        return

    # Далее строим стандартную info карточку (твой старый код)
    event_deleted = False
    try:
        parts = await _build_info_parts(event.client, force_fallback=False)
        media_pointer_str = db.get_setting("profile_media")
        media = None
        if media_pointer_str:
            try:
                pointer = json.loads(media_pointer_str)
                message_to_fetch = await event.client.get_messages(pointer["chat_id"], ids=pointer["message_id"])
                if message_to_fetch and message_to_fetch.media:
                    media = message_to_fetch.media
            except Exception:
                db.set_setting("profile_media", "")
        if media:
            text, entities = build_message(parts)
            await event.client.send_file(
                event.chat_id,
                media,
                caption=text,
                formatting_entities=entities,
                link_preview=False
            )
            await event.delete()
            event_deleted = True
        else:
            await build_and_edit(event, parts, link_preview=False)
    except (DocumentInvalidError, MessageIdInvalidError) as e:
        error_text = (
            "❌ **Ошибка рендера .info!**\n\n"
            "Один из премиум-эмодзи в `.setpemoji`, `.setosemoji`, `.setbio` или `.addfield` "
            "битый или недоступен для твоего юзербота.\n\n"
            f"`{type(e).__name__}`\n\n"
            "👇 **Показываю .info в \"безопасном режиме\" (без премиум-эмодзи):**"
        )
        try:
            safe_parts = await _build_info_parts(event.client, force_fallback=True)
            safe_text, safe_entities = build_message(safe_parts)
            final_error_text = f"{error_text}\n\n{safe_text}"
            if event_deleted:
                await event.client.send_message(event.chat_id, final_error_text, formatting_entities=safe_entities, parse_mode="md", link_preview=False)
            else:
                await event.edit(final_error_text, formatting_entities=safe_entities, parse_mode="md", link_preview=False)
        except Exception as e2:
            error_text = f"❌ **Двойная ошибка .info!**\n\nДаже \"безопасный режим\" упал:\n`{type(e2).__name__}: {e2}`"
            if not event_deleted:
                await event.edit(error_text)
            else:
                await event.client.send_message(event.chat_id, error_text)
    except Exception as e:
        error_text = f"❌ **Неизвестная ошибка .info:**\n\n`{type(e).__name__}: {e}`"
        if not event_deleted:
            try:
                await event.edit(error_text, parse_mode="md")
            except MessageIdInvalidError:
                await event.client.send_message(event.chat_id, error_text, parse_mode="md")
        else:
             await event.client.send_message(event.chat_id, error_text, parse_mode="md")