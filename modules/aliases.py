# modules/aliases.py
"""<manifest>
version: 1.1.0
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/aliases.py
author: Kote

Команды:
• alias <новый_алиас> <команда> - Создать псевдоним.
• unalias <алиас> - Удалить псевдоним.
• aliases - Список псевдонимов.
</manifest>"""

from core import register
from utils import database as db
from utils.loader import COMMANDS_REGISTRY, reload_module
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji, MessageEntityItalic

# --- PREMIUM EMOJIS ---
TAG_ID = 5987802868734760945      # 🏷 (Тэг)
BOX_ID = 5884479287171485878      # 📦 (Коробка)
ARROW_ID = 5224459688426354697    # ➡️ (Стрелка)
SUCCESS_ID = 5776375003280838798  # ✅
ERROR_ID = 5778527486270770928    # ❌
TRASH_ID = 6039522349517115015    # 🗑
RELOAD_ID = 6030657343744644592   # 🔄
INFO_ID = 6028435952299413210     # ℹ️

@register("alias", incoming=True)
async def add_alias_cmd(event):
    """Создает новый алиас для существующей команды."""
    if not check_permission(event, min_level="OWNER"):
        return

    args = event.message.text.split(maxsplit=2)
    if len(args) < 3:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
            {"text": " Использование: ", "entity": MessageEntityBold},
            {"text": ".alias <новый_алиас> <существующая_команда>", "entity": MessageEntityCode}
        ])

    new_alias = args[1].lower()
    real_command = args[2].lower()

    # --- ПРОВЕРКА 1: Конфликт с реальными командами ---
    if new_alias in COMMANDS_REGISTRY:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
            {"text": " Имя ", "entity": MessageEntityBold},
            {"text": new_alias, "entity": MessageEntityCode},
            {"text": " уже занято реальной командой модуля ", "entity": MessageEntityBold},
            {"text": COMMANDS_REGISTRY[new_alias][0]["module"], "entity": MessageEntityCode},
            {"text": ". Придумайте другое."}
        ])

    # --- ПРОВЕРКА 2: Конфликт с другими алиасами ---
    existing_aliases = db.get_all_aliases()
    for row in existing_aliases:
        if row['alias'] == new_alias:
            return await build_and_edit(event, [
                {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
                {"text": " Алиас ", "entity": MessageEntityBold},
                {"text": new_alias, "entity": MessageEntityCode},
                {"text": " уже существует (ведет на ", "entity": MessageEntityBold},
                {"text": row['real_command'], "entity": MessageEntityCode},
                {"text": "). Удалите его сначала."}
            ])

    # --- ПРОВЕРКА 3: Существует ли целевая команда ---
    if real_command not in COMMANDS_REGISTRY:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
            {"text": " Команда ", "entity": MessageEntityBold},
            {"text": real_command, "entity": MessageEntityCode},
            {"text": " не найдена."}
        ])

    # --- ОПРЕДЕЛЕНИЕ МОДУЛЯ ---
    # Если команд несколько (конфликт модулей), берем ПЕРВЫЙ зарегистрированный.
    # Это стандартное поведение загрузчика.
    module_name = COMMANDS_REGISTRY[real_command][0]["module"]

    # Сохраняем
    db.add_alias(new_alias, real_command, module_name)

    await build_and_edit(event, [
        {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_ID}},
        {"text": " Алиас ", "entity": MessageEntityBold},
        {"text": new_alias, "entity": MessageEntityCode},
        {"text": " сохранен.\n"},
        {"text": "🔄", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": RELOAD_ID}},
        {"text": " Перезагружаю модуль ", "entity": MessageEntityBold},
        {"text": module_name, "entity": MessageEntityCode},
        {"text": "..."}
    ])

    await reload_module(event.client, module_name)
    
    await build_and_edit(event, [
        {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_ID}},
        {"text": " Алиас ", "entity": MessageEntityBold},
        {"text": new_alias, "entity": MessageEntityCode},
        {"text": " активен!"}
    ])

@register("unalias", incoming=True)
async def remove_alias_cmd(event):
    """Удаляет существующий алиас."""
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

@register("aliases", incoming=True)
async def list_aliases_cmd(event):
    """Показывает список всех алиасов с красивым форматированием."""
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
            # Красивая стрелочка (премиум)
            parts.append({"text": " "})
            parts.append({"text": "➡️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ARROW_ID}})
            parts.append({"text": f" {real}\n", "entity": MessageEntityItalic})
        
        parts.append({"text": "\n"})

    await build_and_edit(event, parts)