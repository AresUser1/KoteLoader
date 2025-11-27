# modules/private.py
"""
<manifest>
version: 1.0.3
source: local
author: Kote
</manifest>

Приватный менеджер модулей.
Позволяет устанавливать модули из локального приватного хранилища.
"""

import os
import shutil
from pathlib import Path
from telethon.tl.custom import Button

from core import register, inline_handler, callback_handler
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission
from handlers.user_commands import _call_inline_bot

PRIVATE_DIR = Path("/home/kote/KoteBot/Private")
MODULES_DIR = Path(__file__).parent.parent / "modules"

LOCK_ID = 5778570255555105942
STORE_ID = 5932662492856585848
INSTALL_ID = 5877540355187937244

@register("private", incoming=True)
async def private_cmd(event):
    """Открыть приватное хранилище.
    
    Usage: {prefix}private
    """
    if not check_permission(event, min_level="OWNER"): return

    if not PRIVATE_DIR.exists():
        from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityBold
        return await build_and_edit(event, [
            {"text": "🔒", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": LOCK_ID}},
            {"text": " Ошибка доступа.", "entity": MessageEntityBold},
            {"text": "\nСервер не авторизован для доступа к приватным модулям."},
            {"text": "\n(Папка Private не найдена)"}
        ])

    await build_and_edit(event, [{"text": "🔐 Открываю хранилище..."}])
    await _call_inline_bot(event, "private:list")

@inline_handler(r"private:list", title="Приватные модули", description="Установка приватных модулей")
async def private_list_inline(event):
    if not PRIVATE_DIR.exists():
        return "❌ <b>Доступ запрещен.</b>", [Button.inline("Закрыть", data="close_panel")]

    files = [f for f in PRIVATE_DIR.glob("*.py") if f.is_file()]
    
    if not files:
        return "📂 <b>Приватное хранилище пусто.</b>", [Button.inline("Закрыть", data="close_panel")]

    text = f"🔐 <b>Приватное хранилище</b>\n\nДоступно модулей: {len(files)}"
    buttons = []
    
    for f in files:
        buttons.append([Button.inline(f"📥 Установить {f.name}", data=f"private:install:{f.name}")])
    
    buttons.append([Button.inline("❌ Закрыть", data="close_panel")])
    return text, buttons

@callback_handler(r"private:install:(.+)")
async def private_install_cb(event):
    # ❗️ ИСПРАВЛЕНИЕ: Убрал .decode(), т.к. библиотека уже декодирует
    filename = event.pattern_match.group(1)
    source_file = PRIVATE_DIR / filename
    
    if not source_file.exists():
        return await event.answer("❌ Файл не найден!", alert=True)

    await event.answer(f"Устанавливаю {filename}...")
    
    try:
        dest_file = MODULES_DIR / filename
        shutil.copy(source_file, dest_file)
        
        from utils.loader import load_module
        module_name = filename[:-3]
        await load_module(event.client.user_client, module_name)
        
        # ❗️ Вернул HTML разметку
        await event.edit(f"✅ <b>Модуль {module_name} успешно установлен!</b>", parse_mode="html")
        
    except Exception as e:
        # ❗️ Вернул HTML разметку
        await event.edit(f"❌ <b>Ошибка установки:</b>\n<code>{e}</code>", parse_mode="html")