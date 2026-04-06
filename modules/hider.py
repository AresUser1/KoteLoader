# modules/hider.py
"""
<manifest>
version: 1.0.2
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/hider.py
author: Kote
</manifest>

Модуль для скрытия других модулей из списка .help.
"""

from core import register, Module
from utils import database as db
from utils.security import check_permission
from utils.message_builder import build_and_edit
from telethon.tl.types import MessageEntityBold, MessageEntityCode

class HiderModule(Module):

    @register("hide", outgoing=True)
    async def hide_cmd(self, event):
        """Скрывает модуль из списка .help.
        
        Usage: {prefix}hide <название>
        """
        if not check_permission(event, min_level="TRUSTED"):
            return
            
        module_to_hide = event.pattern_match.group(1)
        if not module_to_hide:
            return await build_and_edit(event, [{"text": "❌ **Укажите модуль, который нужно скрыть.**"}])

        db.hide_module(module_to_hide.lower())
        await build_and_edit(event, [
            {"text": "✅ Модуль "},
            {"text": module_to_hide, "entity": MessageEntityCode},
            {"text": " **скрыт из .help**."}
        ])

    @register("unhide", outgoing=True)
    async def unhide_cmd(self, event):
        """Возвращает модуль в список .help.
        
        Usage: {prefix}unhide <название>
        """
        if not check_permission(event, min_level="TRUSTED"):
            return
            
        module_to_unhide = event.pattern_match.group(1)
        if not module_to_unhide:
            return await build_and_edit(event, [{"text": "❌ **Укажите модуль, который нужно вернуть в .help**."}])

        db.unhide_module(module_to_unhide.lower())
        await build_and_edit(event, [
            {"text": "✅ Модуль "},
            {"text": module_to_unhide, "entity": MessageEntityCode},
            {"text": " **снова виден в .help**."}
        ])

    @register("hidden", outgoing=True)
    async def hidden_cmd(self, event):
        """Показывает список скрытых модулей.
        
        Usage: {prefix}hidden
        """
        if not check_permission(event, min_level="TRUSTED"):
            return
            
        hidden_list = db.get_hidden_modules()
        if not hidden_list:
            return await build_and_edit(event, [{"text": "ℹ️ **Нет скрытых модулей.**"}])

        text = "**Скрытые модули:**\n" + " ".join([f"`{mod}`" for mod in sorted(hidden_list)])
        await event.edit(text, parse_mode="md")