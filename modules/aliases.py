# modules/aliases.py
"""
<manifest>
version: 1.2.1
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/aliases.py
author: Kote
</manifest>

Модуль для создания псевдонимов (алиасов) для команд.
Позволяет переименовывать существующие команды и разрешать конфликты имен между модулями.
"""

from telethon import events
from telethon.tl.custom import Button
from core import register, callback_handler
from utils import database as db
from utils.loader import COMMANDS_REGISTRY, reload_module
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji, MessageEntityItalic

TAG_ID = 5843862283964390528      
BOX_ID = 5256094480498436162      
ARROW_ID = 5467906619964695429    
SUCCESS_ID = 5255813619702049821  
ERROR_ID = 5985346521103604145    
TRASH_ID = 5255831443816327915    
RELOAD_ID = 5877410604225924969   
INFO_ID = 5256230583717079814     
QUESTION_ID = 6030784887093464891 
WRENCH_ID = 5258023599419171861   

PENDING_RESOLUTIONS = {}

@register("alias")
async def add_alias_cmd(event):
    """Создает новый алиас.
    
    Usage: {prefix}alias <новый_алиас> <команда>
    """
    if not check_permission(event, min_level="OWNER"):
        return

    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=2)
    
    if len(args) < 3:
        return await build_and_edit(event, [
            {"text": "ℹ️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": INFO_ID}},
            {"text": " Использование: ", "entity": MessageEntityBold},
            {"text": f"{prefix}alias <новый_алиас> <команда>", "entity": MessageEntityCode}
        ])

    new_alias = args[1].lower()
    real_command = args[2].lower()

    if new_alias in COMMANDS_REGISTRY:
        owner_mod = COMMANDS_REGISTRY[new_alias][0]["module"]
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
            {"text": " Имя ", "entity": MessageEntityBold},
            {"text": new_alias, "entity": MessageEntityCode},
            {"text": " уже занято командой модуля ", "entity": MessageEntityBold},
            {"text": owner_mod, "entity": MessageEntityCode},
            {"text": "."}
        ])

    existing_aliases = db.get_all_aliases()
    for row in existing_aliases:
        if row['alias'] == new_alias:
            return await build_and_edit(event, [
                {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
                {"text": " Алиас ", "entity": MessageEntityBold},
                {"text": new_alias, "entity": MessageEntityCode},
                {"text": " уже существует (ведет на ", "entity": MessageEntityBold},
                {"text": row['real_command'], "entity": MessageEntityCode},
                {"text": ")."}
            ])

    if real_command not in COMMANDS_REGISTRY:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
            {"text": " Команда ", "entity": MessageEntityBold},
            {"text": real_command, "entity": MessageEntityCode},
            {"text": " не найдена."}
        ])

    possible_matches = COMMANDS_REGISTRY[real_command]
    unique_modules = list(set([m['module'] for m in possible_matches]))

    if len(unique_modules) > 1:
        PENDING_RESOLUTIONS[event.sender_id] = {
            'new': new_alias,
            'real': real_command
        }
        
        buttons = []
        for mod_name in unique_modules:
            buttons.append([
                Button.inline(f"{real_command} ({mod_name})", data=f"al_res:{mod_name}")
            ])
        
        buttons.append([Button.inline("❌ Отмена", data="al_res:cancel")])

        await build_and_edit(event, [
            {"text": "❓", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": QUESTION_ID}},
            {"text": " Найдено несколько модулей с командой ", "entity": MessageEntityBold},
            {"text": real_command, "entity": MessageEntityCode},
            {"text": ".\n\n"},
            {"text": "Выберите, к какому модулю выполнить привязку:", "entity": MessageEntityBold}
        ], buttons=buttons)
        return

    module_name = unique_modules[0]
    await _finalize_alias(event, new_alias, real_command, module_name)


@callback_handler(r"al_res:(.+)")
async def resolve_alias_callback(event):
    user_id = event.sender_id
    if not check_permission(event, min_level="OWNER"):
        return await event.answer("🚫 Доступ запрещен")

    data = event.pattern_match.group(1).decode()
    
    if data == "cancel":
        if user_id in PENDING_RESOLUTIONS:
            del PENDING_RESOLUTIONS[user_id]
        await event.edit("❌ Создание алиаса отменено.", buttons=None)
        return

    if user_id not in PENDING_RESOLUTIONS:
        await event.answer("⚠️ Сессия истекла. Повторите команду.", alert=True)
        await event.delete()
        return

    resolution = PENDING_RESOLUTIONS[user_id]
    new_alias = resolution['new']
    real_command = resolution['real']
    module_name = data 

    del PENDING_RESOLUTIONS[user_id]
    await _finalize_alias(event, new_alias, real_command, module_name)


async def _finalize_alias(event, new_alias, real_command, module_name):
    db.add_alias(new_alias, real_command, module_name)

    loader_msg = [
        {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_ID}},
        {"text": " Алиас сохранен.\n"},
        {"text": "🔄", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": RELOAD_ID}},
        {"text": " Перезагружаю модуль ", "entity": MessageEntityBold},
        {"text": module_name, "entity": MessageEntityCode},
        {"text": "..."}
    ]
    
    try:
        if isinstance(event, events.CallbackQuery.Event):
            await event.edit("".join([p.get('text', '') for p in loader_msg]), buttons=None)
        else:
            await build_and_edit(event, loader_msg)
    except: pass

    client = event.client
    await reload_module(client, module_name)
    
    success_msg = [
        {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_ID}},
        {"text": " Алиас ", "entity": MessageEntityBold},
        {"text": new_alias, "entity": MessageEntityCode},
        {"text": " успешно привязан к ", "entity": MessageEntityBold},
        {"text": f"{real_command} ({module_name})", "entity": MessageEntityCode},
        {"text": "!"}
    ]

    if isinstance(event, events.CallbackQuery.Event):
        await event.edit(
            f"✅ <b>Алиас</b> <code>{new_alias}</code> <b>успешно привязан к</b> <code>{real_command} ({module_name})</code>!", 
            parse_mode="html"
        )
    else:
        await build_and_edit(event, success_msg)


@register("unalias")
async def remove_alias_cmd(event):
    """Удаляет существующий алиас.
    
    Usage: {prefix}unalias <алиас>
    """
    if not check_permission(event, min_level="OWNER"):
        return

    alias_to_remove = event.pattern_match.group(1)
    if not alias_to_remove:
        return await build_and_edit(event, [{"text": "❌ Укажите алиас для удаления."}])

    all_aliases = db.get_all_aliases()
    target_module = None
    found = False
    
    for row in all_aliases:
        if row['alias'] == alias_to_remove:
            target_module = row['module_name']
            found = True
            break
    
    if not found:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
            {"text": " Такой алиас не найден."}
        ])

    db.remove_alias(alias_to_remove)
    
    parts = [
        {"text": "🗑", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": TRASH_ID}},
        {"text": " Алиас ", "entity": MessageEntityBold},
        {"text": alias_to_remove, "entity": MessageEntityCode},
        {"text": " удален."}
    ]
    
    if target_module:
        parts.append({"text": "\n"})
        parts.append({"text": "🔄", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": RELOAD_ID}})
        parts.append({"text": " Перезагружаю модуль..."})
        await build_and_edit(event, parts)
        await reload_module(event.client, target_module)
    else:
        await build_and_edit(event, parts)


@register("aliases")
async def list_aliases_cmd(event):
    """Показывает список всех алиасов.
    
    Usage: {prefix}aliases
    """
    if not check_permission(event, min_level="OWNER"):
        return

    aliases = db.get_all_aliases()
    if not aliases:
        return await build_and_edit(event, [
            {"text": "ℹ️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": INFO_ID}},
            {"text": " Алиасов пока нет.", "entity": MessageEntityBold}
        ])

    parts = [
        {"text": "🏷", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": TAG_ID}},
        {"text": " Список алиасов:", "entity": MessageEntityBold},
        {"text": "\n\n"}
    ]
    
    from collections import defaultdict
    grouped = defaultdict(list)
    
    for row in aliases:
        grouped[row['module_name']].append((row['alias'], row['real_command']))

    for mod_name, items in sorted(grouped.items()):
        parts.append({"text": "📦", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": BOX_ID}})
        parts.append({"text": f" {mod_name}:\n", "entity": MessageEntityBold})
        
        for alias, real in sorted(items):
            parts.append({"text": f"  • "})
            parts.append({"text": alias, "entity": MessageEntityCode})
            parts.append({"text": " "})
            parts.append({"text": "➡️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ARROW_ID}})
            parts.append({"text": f" {real}\n", "entity": MessageEntityItalic})
        
        parts.append({"text": "\n"})

    await build_and_edit(event, parts)