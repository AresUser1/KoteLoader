# modules/logs.py
"""
<manifest>
version: 1.0.2
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/logs.py
author: Kote
</manifest>

Модуль для просмотра и управления логами бота.
Позволяет читать лог-файл с фильтрацией и управлять уровнем отладки.
"""

import logging
from pathlib import Path
from core import register
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityBold, MessageEntityCode

LOGS_EMOJI_ID = 5256230583717079814
SUCCESS_EMOJI_ID = 5255813619702049821
INFO_EMOJI_ID = 5879813604068298387
DEBUG_EMOJI_ID = 5452051336881271172
WARNING_EMOJI_ID = 4915853119839011973
ERROR_EMOJI_ID = 5253877736207821121
CRITICAL_EMOJI_ID = 5116414868357907335
STATS_EMOJI_ID = 5364265190353286344

LOG_FILE = Path(__file__).parent.parent / "kote_loader.log"
MAX_LOG_CHARS = 4000

def parse_log_line(line: str) -> list:
    log_levels = {
        "CRITICAL": CRITICAL_EMOJI_ID, "ERROR": ERROR_EMOJI_ID,
        "WARNING": WARNING_EMOJI_ID, "INFO": INFO_EMOJI_ID, "DEBUG": DEBUG_EMOJI_ID,
    }
    emoji_chars = {"CRITICAL": "🔥", "ERROR": "🔥", "WARNING": "⚠️", "INFO": "ℹ️", "DEBUG": "🐞"}

    for level, emoji_id in log_levels.items():
        if f" - {level} - " in line:
            clean_line = line.split(f" - {level} - ", 1)[1]
            return [
                {"text": emoji_chars[level], "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": emoji_id}},
                {"text": f" {clean_line}\n"}
            ]
    return [{"text": f"▫️ {line}\n"}]

@register("logs", incoming=True)
async def logs_cmd(event):
    """Показывает последние записи из лог-файла.
    
    Usage: {prefix}logs [-n число] [-l уровень] [-f текст]
    """
    if not check_permission(event, min_level="TRUSTED"):
        return

    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split()[1:]
    lines_to_show, level_filter, text_filter = 20, None, None
    
    try:
        if "-n" in args: lines_to_show = int(args[args.index("-n") + 1])
        if "-l" in args: level_filter = args[args.index("-l") + 1].upper()
        if "-f" in args: text_filter = args[args.index("-f") + 1]
    except (ValueError, IndexError):
        return await build_and_edit(event, [
            {"text": "❌ "}, 
            {"text": f"Ошибка: Неверные аргументы. Использование: {prefix}logs [-n число] [-l уровень] [-f текст]", "entity": MessageEntityBold}
        ])

    if not LOG_FILE.exists():
        return await build_and_edit(event, [
            {"text": "📝", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": LOGS_EMOJI_ID}},
            {"text": " Лог-файл не найден.", "entity": MessageEntityBold}
        ])

    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    log_parts, lines_found = [], 0
    for line in reversed(lines):
        if (level_filter and f" - {level_filter} - " not in line) or \
           (text_filter and text_filter.lower() not in line.lower()):
            continue
        
        log_parts.extend(parse_log_line(line.strip()))
        lines_found += 1
        if lines_found >= lines_to_show: break
    
    log_parts.reverse()

    if not log_parts:
        return await build_and_edit(event, [
            {"text": "📝", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": LOGS_EMOJI_ID}},
            {"text": " Записей, соответствующих вашим фильтрам, не найдено.", "entity": MessageEntityBold}
        ])

    header_text = f" Последние {lines_found} записей в логах:"
    final_parts = [
        {"text": "📝", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": LOGS_EMOJI_ID}},
        {"text": header_text, "entity": MessageEntityBold},
        {"text": "\n"}
    ]
    
    full_log_text = "".join([p["text"] for p in log_parts])
    if len(full_log_text.encode()) > 4000:
        with open("logs.txt", "w", encoding="utf-8") as f:
            f.write("".join([p['text'] for p in log_parts]))
        await event.client.send_file(event.chat_id, "logs.txt", caption=header_text)
        return await event.delete()
    
    final_parts.extend(log_parts)
    await build_and_edit(event, final_parts)

@register("debug", incoming=True)
async def debug_cmd(event):
    """Включает или выключает режим отладки.
    
    Usage: {prefix}debug <on|off>
    """
    if not check_permission(event, min_level="TRUSTED"):
        return

    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=1)
    
    if len(args) < 2 or args[1].lower() not in ["on", "off"]:
        is_on = db.get_setting("debug_mode") == "True"
        status_text = "включен" if is_on else "выключен"
        status_emoji_id = DEBUG_EMOJI_ID if is_on else INFO_EMOJI_ID
        status_char = "🐞" if is_on else "ℹ️"
        
        return await build_and_edit(event, [
            {"text": "📊", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": STATS_EMOJI_ID}},
            {"text": " Режим отладки", "entity": MessageEntityBold},
            {"text": "\n\nТекущий статус: "},
            {"text": status_text, "entity": MessageEntityBold},
            {"text": " "},
            {"text": status_char, "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": status_emoji_id}},
            {"text": "\nИспользование: "},
            {"text": f"{prefix}debug on", "entity": MessageEntityCode},
            {"text": " или "},
            {"text": f"{prefix}debug off", "entity": MessageEntityCode}
        ])

    mode = args[1].lower()
    if mode == "on":
        db.set_setting("debug_mode", "True")
        logging.getLogger().setLevel(logging.DEBUG)
        msg, emoji_char, emoji_id = "Режим отладки включен. Теперь в лог будет записываться всё.", "🐞", DEBUG_EMOJI_ID
    else:
        db.set_setting("debug_mode", "False")
        logging.getLogger().setLevel(logging.INFO)
        msg, emoji_char, emoji_id = "Режим отладки выключен.", "ℹ️", INFO_EMOJI_ID

    await build_and_edit(event, [
        {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
        {"text": f" {msg}", "entity": MessageEntityBold},
        {"text": "\n\n"},
        {"text": emoji_char, "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": emoji_id}},
        {"text": " Изменения применены немедленно."}
    ])