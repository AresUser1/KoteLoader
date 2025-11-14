# modules/about.py
"""<manifest>
version: 1.0.1
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/about.py
author: Kote

Команды:
• about - Показывает информацию о KoteLoader.
</manifest>"""

from core import register
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityBold, MessageEntityCustomEmoji, MessageEntityTextUrl

# --- Премиум Эмодзи ---
PC_ID = 5386440626193585237
GRIN_ID = 5769289093221454192
CLIP_ID = 6039451237743595514
THOUGHT_ID = 5904248647972820334

@register("about", incoming=True)
async def about_cmd(event):
    """Показывает информацию о KoteLoader."""
    if not check_permission(event, min_level="TRUSTED"):
        return

    # Пытаемся получить URL репозитория из БД
    repo_url = db.get_setting("repo_url")
    if not repo_url:
        # Если в БД нет, используем тот, что встроен в .updatecore
        repo_url = "https://github.com/AresUser1/KoteLoader" 

    intro_text = (
        "KoteLoader — это мощный и гибкий модульный юзербот для Telegram, "
        "созданный для кастомизации и расширения ваших возможностей. "
        "Он позволяет вам легко управлять модулями, добавлять новый функционал "
        "и автоматизировать рутинные задачи."
    )

    parts = [
        {"text": "KoteLoader", "entity": MessageEntityBold},
        {"text": f"\n\n{intro_text}"},
        {"text": "\n\n"},

        # ❗️❗️❗️ ИЗМЕНЕНИЕ: Ссылки встроены в текст ❗️❗️❗️

        # 1. GitHub
        {"text": "🖥", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": PC_ID}},
        {"text": " "}, # Пробел
        {"text": "GitHub KoteLoader", "entity": MessageEntityTextUrl, "kwargs": {"url": repo_url}},
        {"text": "\n"},

        # 2. Telegram Chat
        {"text": "😀", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": GRIN_ID}},
        {"text": " "}, # Пробел
        {"text": "Telegram KoteLoader", "entity": MessageEntityTextUrl, "kwargs": {"url": "https://t.me/KoteLoader"}},
        {"text": "\n"},

        # 3. Modules Channel
        {"text": "📎", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": CLIP_ID}},
        {"text": " "}, # Пробел
        {"text": "Modules KoteLoader", "entity": MessageEntityTextUrl, "kwargs": {"url": "https://t.me/KoteModulesMint"}},
        {"text": "\n"},

        # 4. Developer (Как ты и просил, "кроме @", оставлено как было)
        {"text": "💭", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": THOUGHT_ID}},
        {"text": " Developer: "},
        {"text": "@Aaaggrrr", "entity": MessageEntityTextUrl, "kwargs": {"url": "https://t.me/Aaaggrrr"}},
    ]

    await build_and_edit(event, parts, link_preview=False)