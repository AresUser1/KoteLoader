# modules/profile.py
"""
<manifest>
version: 1.2.0
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/profile.py
author: Kote
</manifest>

Модуль для отображения и настройки информационной карточки профиля (юзербота).
Поддерживает кастомные шаблоны, эмодзи-паки и медиа-вложения.
"""

import time
import platform
import distro
import json
import os
import git
import psutil
from datetime import timedelta
from pathlib import Path

from telethon.tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCode, MessageEntityPre,
    MessageEntityUnderline, MessageEntityStrike, MessageEntityCustomEmoji,
    MessageEntityTextUrl, MessageEntityBlockquote
)
from telethon.errors.rpcerrorlist import (
    MessageNotModifiedError, DocumentInvalidError, MessageIdInvalidError
)

from core import register
from utils import database as db
from main import START_TIME
from utils.message_builder import build_message, build_and_edit
from utils.security import check_permission
from services.module_info_cache import parse_manifest

# --- CONSTANTS & HELPERS ---

def get_uptime() -> str:
    return str(timedelta(seconds=int(time.time() - START_TIME)))

def get_git_info() -> dict:
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

def _get_static_emojis() -> dict:
    DEFAULT_STATIC_EMOJIS = {
        "PAW_1":    {"id": 5266969165893238430, "fallback": "🐾"},
        "PAW_2":    {"id": 5266983901926029702, "fallback": "🐾"},
        "PAW_3":    {"id": 5269523863980504823, "fallback": "🐾"},
        "OWNER":    {"id": 5373141891321699086, "fallback": "😎"},
        "BIO":      {"id": 6030784887093464891, "fallback": "💬"},
        "VERSION":  {"id": 5469741319330996757, "fallback": "💫"},
        "BRANCH":   {"id": 5449918202718985124, "fallback": "🌳"},
        "STATUS":   {"id": 5370699111492229743, "fallback": "😌"},
        "PREFIX":   {"id": 5472111548572900003, "fallback": "⌨️"},
        "UPTIME":   {"id": 5451646226975955576, "fallback": "⌛️"},
        "CPU":      {"id": 5431449001532594346, "fallback": "⚡️"},
        "RAM":      {"id": 5359785904535774578, "fallback": "💼"},
    }
    custom_emojis = db.get_module_data("profile", "static_emojis", default={})
    return {**DEFAULT_STATIC_EMOJIS, **custom_emojis}

def _get_os_emoji_mapping() -> dict:
    DEFAULT_OS_EMOJIS = {
        "Linux":    {"id": 5361541227604878624, "fallback": "🐧"},
        "Ubuntu":   {"id": 4985927121885988299, "fallback": "🟠"},
        "Mint":     {"id": 5276194798594695653, "fallback": "🟢"},
        "Termux":   {"id": 4985572151428907537, "fallback": "⚫️"},
        "JamHost":  {"id": 5422884965593397853, "fallback": "🍓"},
        "Arch":     {"id": 5275984632960001736, "fallback": "🔵"},
        "Debian":   {"id": 4983489886859297852, "fallback": "🔴"},
        "Fedora":   {"id": 5276032324276855015, "fallback": "🔵"},
        "Windows":  {"id": 4985790451731661389, "fallback": "🪟"},
        "macOS":    {"id": 4985915392330302373, "fallback": "🍏"},
        "Other":    {"id": 5276027711481981374, "fallback": "💻"}
    }
    custom_emojis = db.get_module_data("profile", "os_emojis", default={})
    return {**DEFAULT_OS_EMOJIS, **custom_emojis}

def _build_emoji_part(emoji_details: dict, force_fallback: bool = False) -> dict:
    part = {"text": emoji_details.get('fallback', '❔')}
    if emoji_details.get('id') != 0 and not force_fallback:
        part["entity"] = MessageEntityCustomEmoji
        part["kwargs"] = {"document_id": emoji_details['id']}
    return part

def get_os_display_name():
    try:
        dist_name = distro.name(pretty=True)
        if dist_name and dist_name.lower() != 'linux':
            return dist_name
        return distro.name(pretty=False)
    except ImportError:
        return platform.system()
    except Exception:
        return platform.system()

def get_system_info() -> dict:
    process = psutil.Process(os.getpid())
    cpu_usage = process.cpu_percent()
    ram_usage = process.memory_info().rss / (1024 * 1024)
    
    os_name_display = get_os_display_name()
    os_name_key = "Other"

    if os.environ.get("TERMUX_VERSION"):
        os_name_key = "Termux"
    else:
        system = platform.system()
        if system == "Linux":
            hostname = platform.node().lower()
            if "jam" in hostname:
                os_name_key = "JamHost"
            else:
                try:
                    dist_id = distro.id().lower()
                    if "ubuntu" in dist_id: os_name_key = "Ubuntu"
                    elif "mint" in dist_id: os_name_key = "Mint"
                    elif "arch" in dist_id: os_name_key = "Arch"
                    elif "debian" in dist_id: os_name_key = "Debian"
                    elif "fedora" in dist_id: os_name_key = "Fedora"
                    else: os_name_key = "Linux"
                except Exception:
                    os_name_key = "Linux"
        elif system == "Windows": os_name_key = "Windows"
        elif system == "Darwin": os_name_key = "macOS"
        else: os_name_key = system if system else "Other"

    os_emoji_mapping = _get_os_emoji_mapping()
    os_emoji_details = os_emoji_mapping.get(os_name_key, os_emoji_mapping["Other"])
    return {"cpu": cpu_usage, "ram": ram_usage, "os_name": os_name_display, "os_emoji": os_emoji_details}

async def _build_info_parts(client, force_fallback: bool = False) -> list:
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
    
    try:
        current_file_path = Path(__file__)
        content = current_file_path.read_text(encoding='utf-8')
        manifest = parse_manifest(content)
        version = manifest.get("version", "N/A")
    except Exception:
        version = "N/A"

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
        parts.append({"text": bio_text, "entities": bio_entities if bio_entities else None})
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
            parts.append({"text": field_text, "entities": field_entities if field_entities else None})
            parts.append({"text": "\n"})
        parts.append({"text": "\n"}) 

    parts.append(_build_emoji_part(emojis['VERSION'], force_fallback))
    parts.append({"text": f" Версия: {version} ", "entity": MessageEntityBold})
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

async def _parse_emoji_args(event, cmd_name: str, example_key: str) -> dict:
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
            return {"error": [{"text": "❌ ... ID должен быть числом ..."}]}
    else:
        return {"error": [{"text": "❌ ... Укажите ID или Премиум-Эмодзи ..."}]}
    
    if fallback_char == "❔" and emoji_id != 0:
         return {"error": [{"text": "❌ ... Укажите fallback-символ ..."}]}
            
    return {"key": key, "id": emoji_id, "fallback": fallback_char}

# --- COMMANDS ---

@register("setbio", incoming=True)
async def setbio_cmd(event):
    """Установить/изменить био профиля.
    
    Usage: {prefix}setbio <текст>
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    prefix = db.get_setting('prefix', '.')
    if not event.pattern_match.group(1):
        return await build_and_edit(event, [
            {"text": "❌ Вы не указали текст."},
            {"text": f"\nПример: {prefix}setbio Моё новое био", "entity": MessageEntityCode},
        ])

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
    await build_and_edit(event, [
        {"text": "✅ "}, 
        {"text": "Био (с форматированием) обновлено!", "entity": MessageEntityBold}
    ])

@register("addfield", incoming=True)
async def addfield_cmd(event):
    """Добавить кастомное поле в профиль.
    
    Usage: {prefix}addfield <название> | <значение>
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    args = event.pattern_match.group(1)
    if not args or "|" not in args:
        return await build_and_edit(event, [{"text": "❌ Неверный формат. Используйте разделитель |"}])
    
    try:
        split_pos = args.find("|")
        name, value_raw = args[:split_pos].strip(), args[split_pos+1:].strip()
    except Exception:
         return await build_and_edit(event, [{"text": "❌ Ошибка разбора."}])
    if not name or not value_raw:
        return await build_and_edit(event, [{"text": "❌ Название и значение не могут быть пустыми."}])

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
    await build_and_edit(event, [{"text": f"✅ Поле «{name}» добавлено."}])

@register("delfield", incoming=True)
async def delfield_cmd(event):
    """Удалить кастомное поле из профиля.
    
    Usage: {prefix}delfield <название>
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    name = event.pattern_match.group(1)
    if not name:
        return await build_and_edit(event, [{"text": "❌ Укажите название поля."}])
    fields = db.get_module_data("profile", "fields_data_v2", default={})
    if name in fields:
        del fields[name]
        db.set_module_data("profile", "fields_data_v2", fields)
        await build_and_edit(event, [{"text": f"🗑️ Поле «{name}» удалено."}])
    else:
        await build_and_edit(event, [{"text": "ℹ️ Поле не найдено."}])

@register("setpfp", incoming=True)
async def setpfp_cmd(event):
    """Установить медиа (фото/видео) для карточки профиля.
    
    Usage: {prefix}setpfp (реплаем на медиа)
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    reply = await event.get_reply_message()
    if not reply or not reply.media:
        return await build_and_edit(event, [{"text": "❌ Ответьте на фото или видео."}])
    pointer = {"chat_id": reply.chat_id, "message_id": reply.id}
    db.set_setting("profile_media", json.dumps(pointer))
    await build_and_edit(event, [
        {"text": "✅ "}, 
        {"text": "Медиа для профиля установлено!", "entity": MessageEntityBold}
    ])

@register("delpfp", incoming=True)
async def delpfp_cmd(event):
    """Удалить медиа из карточки профиля.
    
    Usage: {prefix}delpfp
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    db.set_setting("profile_media", "")
    await build_and_edit(event, [
        {"text": "🗑️ "}, 
        {"text": "Медиа для профиля удалено.", "entity": MessageEntityBold}
    ])

@register("setpemoji", incoming=True)
async def setpemoji_cmd(event):
    """Установить статичный эмодзи (CPU, RAM, и т.д.).
    
    Usage: {prefix}setpemoji <ключ> <эмодзи/ID> | <fallback>
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    parsed = await _parse_emoji_args(event, "setpemoji", "OWNER")
    if "error" in parsed:
        return await build_and_edit(event, parsed["error"])
    key_upper = parsed["key"].upper()
    if key_upper not in _get_static_emojis():
        return await build_and_edit(event, [{"text": "❌ Неизвестный ключ эмодзи."}])
    custom_emojis = db.get_module_data("profile", "static_emojis", default={})
    custom_emojis[key_upper] = {"id": parsed["id"], "fallback": parsed["fallback"]}
    db.set_module_data("profile", "static_emojis", custom_emojis)
    await build_and_edit(event, [
        {"text": "✅ "}, 
        {"text": f"Эмодзи для {key_upper} установлен!", "entity": MessageEntityBold}
    ])

@register("delpemoji", incoming=True)
async def delpemoji_cmd(event):
    """Сбросить статичный эмодзи.
    
    Usage: {prefix}delpemoji <ключ>
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    key_upper = (event.pattern_match.group(1) or "").upper()
    if not key_upper:
        return await build_and_edit(event, [{"text": "❌ Укажите ключ."}])
    custom_emojis = db.get_module_data("profile", "static_emojis", default={})
    if key_upper in custom_emojis:
        del custom_emojis[key_upper]
        db.set_module_data("profile", "static_emojis", custom_emojis)
        await build_and_edit(event, [{"text": f"🗑️ Эмодзи {key_upper} сброшен."}])
    else:
        await build_and_edit(event, [{"text": "ℹ️ Кастомный эмодзи не найден."}])

@register("pemojis", incoming=True)
async def pemojis_cmd(event):
    """Показать список всех статичных ключей эмодзи.
    
    Usage: {prefix}pemojis
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    parts = [{"text": "⚙️ Настройки эмодзи профиля:\n\n"}]
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
    """Установить эмодзи для конкретной ОС.
    
    Usage: {prefix}setosemoji <OS> <эмодзи/ID> | <fallback>
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    parsed = await _parse_emoji_args(event, "setosemoji", "Ubuntu")
    if "error" in parsed:
        return await build_and_edit(event, parsed["error"])
    os_name_capitalized = parsed["key"].capitalize()
    custom_emojis = db.get_module_data("profile", "os_emojis", default={})
    custom_emojis[os_name_capitalized] = {"id": parsed["id"], "fallback": parsed["fallback"]}
    db.set_module_data("profile", "os_emojis", custom_emojis)
    await build_and_edit(event, [
        {"text": "✅ "}, 
        {"text": f"Эмодзи для {os_name_capitalized} установлен!", "entity": MessageEntityBold}
    ])

@register("delosemoji", incoming=True)
async def delosemoji_cmd(event):
    """Сбросить эмодзи для ОС.
    
    Usage: {prefix}delosemoji <OS>
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    os_name = (event.pattern_match.group(1) or "").capitalize()
    if not os_name:
        return await build_and_edit(event, [{"text": "❌ Укажите название ОС."}])
    custom_emojis = db.get_module_data("profile", "os_emojis", default={})
    if os_name in custom_emojis:
        del custom_emojis[os_name]
        db.set_module_data("profile", "os_emojis", custom_emojis)
        await build_and_edit(event, [{"text": f"🗑️ Эмодзи {os_name} сброшен."}])
    else:
        await build_and_edit(event, [{"text": "ℹ️ Кастомный эмодзи не найден."}])

@register("osemojis", incoming=True)
async def osemojis_cmd(event):
    """Показать список эмодзи ОС.
    
    Usage: {prefix}osemojis
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    parts = [{"text": "⚙️ Настройки эмодзи ОС:\n\n"}]
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
    """Сбросить ВСЕ кастомные эмодзи .info до заводских настроек.
    
    Usage: {prefix}resetemojis
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    db.set_module_data("profile", "static_emojis", {})
    db.set_module_data("profile", "os_emojis", {})
    await build_and_edit(event, [
        {"text": "🗑️ "}, 
        {"text": "Все кастомные эмодзи сброшены.", "entity": MessageEntityBold}
    ])

@register("setinfo", incoming=True)
async def setinfo_cmd(event):
    """Установить полностью кастомный шаблон .info.
    
    Usage: {prefix}setinfo <текст> (или реплаем)
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    prefix = db.get_setting('prefix', '.')
    raw = event.raw_text or event.text or ''
    cmd_prefix = f"{prefix}setinfo"
    text_content = ''
    entities_list = []

    if raw.lower().startswith(cmd_prefix.lower()):
        text_content = raw[len(cmd_prefix):].lstrip()
        if text_content:
            content_offset = len(raw) - len(text_content)
            if event.entities:
                for e in event.entities:
                    if e.offset >= content_offset:
                        new_e = e.to_dict()
                        new_e['offset'] = new_e['offset'] - content_offset
                        entities_list.append(new_e)
    if not text_content:
        reply = await event.get_reply_message()
        if reply and (reply.raw_text or reply.text):
            text_content = reply.raw_text or reply.text
            if reply.entities:
                entities_list = [e.to_dict() for e in reply.entities]
    if not text_content:
        return await build_and_edit(event, [
            {"text": "❌ Вы не указали текст."},
            {"text": f"\nПример: {prefix}setinfo Привет!", "entity": MessageEntityCode},
        ])
    info_data = {"text": text_content, "entities": entities_list}
    db.set_module_data("profile", "custom_info_v2", info_data)
    await build_and_edit(event, [
        {"text": "✅ ", "entity": MessageEntityBold},
        {"text": "Кастомное .info установлено!", "entity": MessageEntityBold}
    ])

@register("delinfo", incoming=True)
async def delinfo_cmd(event):
    """Сбросить кастомный шаблон .info до стандарта.
    
    Usage: {prefix}delinfo
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    db.set_module_data("profile", "custom_info_v2", None)
    await build_and_edit(event, [
        {"text": "🗑️ ", "entity": MessageEntityBold},
        {"text": "Кастомное .info удалено.", "entity": MessageEntityBold}
    ])

@register("infovars", incoming=True)
async def infovars_cmd(event):
    """Показать список переменных для шаблона .setinfo.
    
    Usage: {prefix}infovars
    """
    if not check_permission(event, min_level="TRUSTED"):
        return

    prefix = db.get_setting("prefix", default=".")
    try:
        emojis = _get_static_emojis()
        emoji_part = _build_emoji_part(emojis.get('PREFIX', {"id": 0, "fallback": "⌨️"}))
    except NameError:
        emoji_part = {"text": "⌨️"}

    parts = [
        emoji_part,
        {"text": " Переменные для ", "entity": MessageEntityBold},
        {"text": f"{prefix}setinfo\n\n", "entity": MessageEntityCode},
        
        {"text": "• {owner}", "entity": MessageEntityCode}, {"text": " - Владелец\n"},
        {"text": "• {me}", "entity": MessageEntityCode}, {"text": " - Ваше имя\n"},
        {"text": "• {prefix}", "entity": MessageEntityCode}, {"text": " - Префикс\n"},
        {"text": "• {uptime}", "entity": MessageEntityCode}, {"text": " - Аптайм\n"},
        {"text": "• {cpu}", "entity": MessageEntityCode}, {"text": " - CPU %\n"},
        {"text": "• {ram}", "entity": MessageEntityCode}, {"text": " - RAM MB\n"},
        {"text": "• {os}", "entity": MessageEntityCode}, {"text": " - ОС\n"},
        {"text": "• {version}", "entity": MessageEntityCode}, {"text": " - Версия\n"},
        {"text": "• {branch}", "entity": MessageEntityCode}, {"text": " - Ветка Git\n"},
        {"text": "• {commit}", "entity": MessageEntityCode}, {"text": " - Хэш Git\n\n"},

        {"text": "Эмодзи:\n", "entity": MessageEntityBold},
        {"text": "{emoji:KEY}", "entity": MessageEntityCode},
        {"text": " (см. pemojis)\n"},
        {"text": "{emoji:os_emoji}", "entity": MessageEntityCode},
        {"text": " (текущая ОС)"},
    ]
    await build_and_edit(event, parts)

@register("info", incoming=True)
async def profile_cmd(event):
    """Отобразить информационную карточку.
    
    Usage: {prefix}info
    """
    if not check_permission(event, min_level="TRUSTED"):
        return

    custom_info = db.get_module_data("profile", "custom_info_v2", default=None)
    
    if custom_info:
        # LOGIC FOR CUSTOM INFO RENDERING
        ENTITY_MAP = {
            'MessageEntityBold': MessageEntityBold, 'MessageEntityItalic': MessageEntityItalic,
            'MessageEntityCode': MessageEntityCode, 'MessageEntityPre': MessageEntityPre,
            'MessageEntityUnderline': MessageEntityUnderline, 'MessageEntityStrike': MessageEntityStrike,
            'MessageEntityCustomEmoji': MessageEntityCustomEmoji, 'MessageEntityTextUrl': MessageEntityTextUrl,
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

        original_text = custom_info.get('text', '') or ''
        
        owner_id = db.get_users_by_level("OWNER")[0]
        owner_entity = await event.client.get_entity(owner_id)
        me = await event.client.get_me()
        sys_info = get_system_info()
        git_info = get_git_info()
        prefix = db.get_setting("prefix", default=".")
        try:
            manifest = parse_manifest(Path(__file__).read_text(encoding='utf-8'))
            version = manifest.get("version", "N/A")
        except Exception:
            version = "N/A"

        text_replacements = {
            "{owner}": f"{owner_entity.first_name}",
            "{me}": me.first_name or "User",
            "{uptime}": get_uptime(),
            "{cpu}": f"{sys_info['cpu']:.1f} %",
            "{ram}": f"{sys_info['ram']:.2f} MB",
            "{os}": sys_info['os_name'],
            "{prefix}": prefix,
            "{version}": version,
            "{branch}": git_info['branch'],
            "{commit}": git_info['commit_sha'],
            "{status}": git_info['status'],
        }

        emoji_replacements = {}
        static_emojis = _get_static_emojis()
        for key, details in static_emojis.items():
            emoji_replacements[f"{{emoji:{key.upper()}}}"] = details
        emoji_replacements["{emoji:os_emoji}"] = sys_info['os_emoji']

        entities = reconstruct_entities(custom_info.get('entities') or [])
        text = original_text

        # Template Replacement Logic
        if any(ph in original_text for ph in text_replacements) or any(ph in original_text for ph in emoji_replacements):
            new_text = ""
            new_entities = []
            entity_tracking = {} 
            i = 0 
            while i < len(text):
                replaced = False
                for placeholder, value in text_replacements.items():
                    if text[i:i+len(placeholder)] == placeholder:
                        utf16_pos_before = len(new_text.encode('utf-16-le')) // 2
                        new_text += value
                        old_utf16_pos = len(text[:i].encode('utf-16-le')) // 2
                        for entity in entities:
                            if entity.offset <= old_utf16_pos < entity.offset + entity.length:
                                entity_id = id(entity)
                                if entity_id not in entity_tracking:
                                    entity_tracking[entity_id] = {'start': utf16_pos_before, 'end': len(new_text.encode('utf-16-le')) // 2, 'entity': entity}
                                else:
                                    entity_tracking[entity_id]['end'] = len(new_text.encode('utf-16-le')) // 2
                        i += len(placeholder)
                        replaced = True
                        break

                if not replaced:
                    for placeholder, details in emoji_replacements.items():
                        if text[i:i+len(placeholder)] == placeholder:
                            utf16_pos_before = len(new_text.encode('utf-16-le')) // 2
                            value = details['fallback']
                            new_text += value
                            if details.get('id', 0) != 0:
                                new_len_utf16 = len(value.encode('utf-16-le')) // 2
                                new_entities.append(MessageEntityCustomEmoji(offset=utf16_pos_before, length=new_len_utf16, document_id=details['id']))
                            old_utf16_pos = len(text[:i].encode('utf-16-le')) // 2
                            for entity in entities:
                                if entity.offset <= old_utf16_pos < entity.offset + entity.length:
                                    entity_id = id(entity)
                                    if entity_id not in entity_tracking:
                                        entity_tracking[entity_id] = {'start': utf16_pos_before, 'end': len(new_text.encode('utf-16-le')) // 2, 'entity': entity}
                                    else:
                                        entity_tracking[entity_id]['end'] = len(new_text.encode('utf-16-le')) // 2
                            i += len(placeholder)
                            replaced = True
                            break

                if not replaced:
                    utf16_pos_before = len(new_text.encode('utf-16-le')) // 2
                    new_text += text[i]
                    old_utf16_pos = len(text[:i].encode('utf-16-le')) // 2
                    for entity in entities:
                        if entity.offset <= old_utf16_pos < entity.offset + entity.length:
                            entity_id = id(entity)
                            if entity_id not in entity_tracking:
                                entity_tracking[entity_id] = {'start': utf16_pos_before, 'end': len(new_text.encode('utf-16-le')) // 2, 'entity': entity}
                            else:
                                entity_tracking[entity_id]['end'] = len(new_text.encode('utf-16-le')) // 2
                    i += 1

            for tracked in entity_tracking.values():
                old_entity = tracked['entity']
                new_length = tracked['end'] - tracked['start']
                if new_length > 0: 
                    # Re-map standard entities
                    if isinstance(old_entity, MessageEntityBlockquote): new_entities.append(MessageEntityBlockquote(offset=tracked['start'], length=new_length))
                    elif isinstance(old_entity, MessageEntityBold): new_entities.append(MessageEntityBold(offset=tracked['start'], length=new_length))
                    elif isinstance(old_entity, MessageEntityItalic): new_entities.append(MessageEntityItalic(offset=tracked['start'], length=new_length))
                    elif isinstance(old_entity, MessageEntityCode): new_entities.append(MessageEntityCode(offset=tracked['start'], length=new_length))
                    elif isinstance(old_entity, MessageEntityUnderline): new_entities.append(MessageEntityUnderline(offset=tracked['start'], length=new_length))
                    elif isinstance(old_entity, MessageEntityStrike): new_entities.append(MessageEntityStrike(offset=tracked['start'], length=new_length))
                    elif isinstance(old_entity, MessageEntityTextUrl): new_entities.append(MessageEntityTextUrl(offset=tracked['start'], length=new_length, url=old_entity.url))
                    elif isinstance(old_entity, MessageEntityCustomEmoji): new_entities.append(MessageEntityCustomEmoji(offset=tracked['start'], length=new_length, document_id=old_entity.document_id))
            text = new_text
            entities = new_entities

        if not any(isinstance(e, MessageEntityBlockquote) for e in entities):
            entities.insert(0, MessageEntityBlockquote(offset=0, length=len(text.encode('utf-16-le')) // 2))

        # Media Handling
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

        try:
            if media:
                await event.client.send_file(event.chat_id, media, caption=text, formatting_entities=entities, link_preview=False)
                await event.delete()
            else:
                await event.edit(text, formatting_entities=entities, link_preview=False)
        except Exception as e:
            fallback_text = f"⚠️ Ошибка рендера:\n`{type(e).__name__}`\n\n{text}"
            if media:
                await event.client.send_file(event.chat_id, media, caption=fallback_text, link_preview=False)
                await event.delete()
            else:
                await event.edit(fallback_text, link_preview=False)
        return
        
    # LOGIC FOR STANDARD INFO RENDERING
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
            await event.client.send_file(event.chat_id, media, caption=text, formatting_entities=entities, link_preview=False)
            await event.delete()
            event_deleted = True
        else:
            await build_and_edit(event, parts, link_preview=False)
    except (DocumentInvalidError, MessageIdInvalidError) as e:
        error_text = (
            "❌ **Ошибка рендера .info!**\n"
            "Премиум-эмодзи недоступны. Показываю безопасный режим."
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
            await event.client.send_message(event.chat_id, f"❌ **Критическая ошибка:** `{e2}`")
    except Exception as e:
        await event.client.send_message(event.chat_id, f"❌ **Ошибка:** `{e}`")