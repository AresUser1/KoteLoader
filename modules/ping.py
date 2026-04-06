# modules/ping.py
"""
<manifest>
version: 1.0.2
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/ping.py
author: Kote
</manifest>

Команды для проверки состояния бота: скорость отклика API и время работы (аптайм).
"""

import time
from datetime import timedelta
from core import register
from utils import database as db
from main import START_TIME
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityBold, MessageEntityCode
from telethon.tl.functions.users import GetUsersRequest

PING_EMOJI_ID = 5431449001532594346    
ROCKET_EMOJI_ID = 5445284980978621387  

def get_uptime() -> str:
    return str(timedelta(seconds=int(time.time() - START_TIME)))

@register("ping", incoming=True)
async def ping_cmd(event):
    """Проверяет скорость ответа API Telegram и аптайм бота.
    
    Usage: {prefix}ping
    """
    if not check_permission(event, min_level="TRUSTED"):
        return

    start = time.time()
    await event.client(GetUsersRequest(id=[await event.client.get_me()]))
    telegram_ping = round((time.time() - start) * 1000, 2)
    
    uptime = get_uptime()
    
    parts = [
        {"text": "⚡️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": PING_EMOJI_ID}},
        {"text": " Скорость отклика Telegram: ", "entity": MessageEntityBold},
        {"text": f"{telegram_ping} мс", "entity": MessageEntityCode},
        {"text": "\n"},
        {"text": "🚀", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ROCKET_EMOJI_ID}},
        {"text": " Время работы: ", "entity": MessageEntityBold},
        {"text": f"{uptime}", "entity": MessageEntityCode}
    ]

    await build_and_edit(event, parts)