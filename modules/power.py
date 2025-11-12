# modules/power.py
"""<manifest>
version: 1.0.0
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/power.py
author: Kote

Команды:
• on - Включить юзербота
• off - Выключить юзербота
</manifest>"""

from utils import database as db
from utils.message_builder import build_and_edit
from core import register
from utils.security import check_permission
from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji

# --- Премиум Эмодзи ---
SUCCESS_EMOJI_ID = 5118861066981344121
POWER_ON_ID = 5818711397860642669
POWER_OFF_ID = 5818665600624365278

@register("on", incoming=True)
async def bot_on_cmd(event):
    """Включает юзербота."""
    # ❗️ ИЗМЕНЕНО: с OWNER на TRUSTED
    if not check_permission(event, min_level="TRUSTED"):
        return

    db.set_setting("userbot_enabled", "True")
    await build_and_edit(event, [
        {"text": "🟢", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": POWER_ON_ID}},
        {"text": " Юзербот включен и принимает команды.", "entity": MessageEntityBold}
    ])

@register("off", incoming=True)
async def bot_off_cmd(event):
    """Выключает юзербота."""
    # ❗️ ИЗМЕНЕНО: с OWNER на TRUSTED
    if not check_permission(event, min_level="TRUSTED"):
        return

    prefix = db.get_setting("prefix", default=".")
    db.set_setting("userbot_enabled", "False")
    await build_and_edit(event, [
        {"text": "🔴", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": POWER_OFF_ID}},
        {"text": " Юзербот выключен.", "entity": MessageEntityBold},
        {"text": "\nОн будет реагировать только на команду "},
        {"text": f"{prefix}on", "entity": MessageEntityCode}
    ])