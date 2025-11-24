# modules/help.py
"""
<manifest>
version: 1.1.2
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/help.py
author: Kote
</manifest>

Модуль справки.
Отображает список доступных модулей и детальную информацию о командах.
"""

from collections import defaultdict
from telethon.tl.types import MessageEntityBlockquote, MessageEntityCustomEmoji, MessageEntityBold, MessageEntityItalic, MessageEntityCode
from core import register
from utils.loader import COMMANDS_REGISTRY, PREFIX
from utils.message_builder import build_and_edit, utf16len
from utils import database as db
from utils.security import check_permission

PAW_EMOJI_ID = 5084923566848213749  
SQUARE_EMOJI_ID_SYSTEM = 4974681956907221809  
SQUARE_EMOJI_ID_USER = 4974508259839836856  
INFO_EMOJI_ID = 5879813604068298387  
USAGE_EMOJI_ID = 5197195523794157505  

SYSTEM_MODULES = ["admin", "help", "install", "modules", "updater", "logs", "ping", "profile", "config", "hider", "power", "git_manager", "core_updater", "about", "aliases", "twins"]

@register("help", incoming=True)
async def help_cmd(event):
    """Показывает справку по командам.
    
    Usage: {prefix}help [команда]
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    args = event.pattern_match.group(1)
    hidden_modules = db.get_hidden_modules()

    async def show_command_help(command_name):
        prefix = db.get_setting("prefix", default=".")
        
        cmd_module = ""
        cmd_info_list = COMMANDS_REGISTRY.get(command_name)
        if cmd_info_list:
            cmd_module = cmd_info_list[0].get("module")

        if not cmd_info_list or cmd_module in hidden_modules:
            return await build_and_edit(event, [
                {"text": "❌ "}, 
                {"text": "Команда ", "entity": MessageEntityBold},
                {"text": command_name, "entity": MessageEntityCode},
                {"text": " не найдена или ее модуль скрыт.", "entity": MessageEntityBold}
            ])

        doc = (cmd_info_list[0].get("doc") or "Без описания").strip()
        
        doc = doc.replace("{prefix}", prefix)

        module_name = cmd_module.capitalize()
        
        if '\nUsage:' in doc:
            description = doc.split('\nUsage:')[0].strip()
            usage_text = doc.split('\nUsage:')[1].strip()
        else:
            description = doc
            usage_text = ""

        parts = [
            {"text": "🐾", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": PAW_EMOJI_ID}},
            {"text": f" {module_name}", "entity": MessageEntityBold},
            {"text": "\n\n"},
            {"text": "ℹ️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": INFO_EMOJI_ID}},
            {"text": f" {description}", "entity": MessageEntityItalic},
            {"text": "\n\n"},
        ]
        
        if usage_text:
            parts.extend([
                {"text": "▫️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": USAGE_EMOJI_ID}},
                {"text": " Использование: ", "entity": MessageEntityBold},
                {"text": f"{prefix}{command_name} {usage_text}" if "{prefix}" not in usage_text and not usage_text.startswith(prefix) else usage_text, "entity": MessageEntityCode},
            ])
        else:
             parts.extend([
                {"text": "▫️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": USAGE_EMOJI_ID}},
                {"text": " Использование: ", "entity": MessageEntityBold},
                {"text": f"{prefix}{command_name}", "entity": MessageEntityCode},
            ])
            
        await build_and_edit(event, parts)

    async def show_all_commands():
        visible_modules = defaultdict(list)
        for command, cmd_info_list in sorted(COMMANDS_REGISTRY.items()):
            module_name = cmd_info_list[0]["module"]
            if module_name not in hidden_modules:
                visible_modules[module_name].append(command)

        text_parts = []
        entities = []
        current_offset = 0

        def append_part(text, entity_type=None, **kwargs):
            nonlocal current_offset
            text_parts.append(text)
            if entity_type:
                length = utf16len(text)
                if length > 0:
                    entities.append(entity_type(offset=current_offset, length=length, **kwargs))
            current_offset += utf16len(text)

        append_part("🐾", MessageEntityCustomEmoji, document_id=PAW_EMOJI_ID)
        append_part(f" {len(visible_modules)} модулей доступно", MessageEntityBold)
        if hidden_modules:
            append_part(f", {len(hidden_modules)} скрыто", MessageEntityBold)
        append_part("\n\n")

        def build_section(title, module_names, emoji_id):
            nonlocal current_offset
            
            section_modules = {name: visible_modules[name] for name in module_names if name in visible_modules}
            if not section_modules:
                return

            append_part(f"{title}\n", MessageEntityBold)
            quote_start_offset = current_offset

            for name, cmds in sorted(section_modules.items()):
                append_part("▪️", MessageEntityCustomEmoji, document_id=emoji_id)
                append_part(f" {name.capitalize()}: ( ", MessageEntityBold)
                cmd_text = " | ".join(sorted(cmds))
                append_part(cmd_text)
                append_part(" )\n")

            quote_end_offset = current_offset
            quote_length = quote_end_offset - quote_start_offset - utf16len('\n')

            if quote_length > 0:
                entities.append(
                    MessageEntityBlockquote(
                        offset=quote_start_offset,
                        length=quote_length,
                        collapsed=True
                    )
                )
            append_part("\n")

        system_module_names = [name for name in visible_modules if name.lower() in SYSTEM_MODULES]
        user_module_names = [name for name in visible_modules if name.lower() not in SYSTEM_MODULES]

        if system_module_names:
            build_section("Системные", system_module_names, SQUARE_EMOJI_ID_SYSTEM)
        
        if user_module_names:
            build_section("Пользовательские", user_module_names, SQUARE_EMOJI_ID_USER)

        final_text = "".join(text_parts).strip()
        
        if event.out:
            await event.edit(final_text, formatting_entities=entities, link_preview=False)
        else:
            await event.respond(final_text, formatting_entities=entities, link_preview=False)

    if args:
        await show_command_help(args)
    else:
        await show_all_commands()