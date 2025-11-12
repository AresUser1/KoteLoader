# modules/config.py
"""<manifest>
version: 1.0.0
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/config.py
author: Kote

Команды:
• setpfp <ответ на медиа> - Установить медиа для профиля
• setrepo <url> - Установить ссылку на репозиторий
</manifest>"""

import json
from core import register
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityBold, MessageEntityCode

@register("setpfp", incoming=True)
async def set_profile_pic(event):
    """Копирует медиа в 'Избранное' и сохраняет ссылку для команды .info."""
    # ❗️ ИЗМЕНЕНО: с OWNER на TRUSTED
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [
            {"text": "🚫 "},
            {"text": "Доступ запрещен.", "entity": MessageEntityBold}
        ])

    reply = await event.get_reply_message()
    if not reply or not reply.media:
        return await build_and_edit(event, [{"text": "Ответьте на фото, видео или гифку.", "entity": MessageEntityBold}])

    await build_and_edit(event, [{"text": "⏳ Сохраняю медиа в ваше 'Избранное'..."}])
    
    try:
        saved_message = await event.client.send_file('me', reply.media)
        media_pointer = {"chat_id": saved_message.chat_id, "message_id": saved_message.id}
        db.set_setting("profile_media", json.dumps(media_pointer))
        
        await build_and_edit(event, [
            {"text": "✅ ",}, 
            {"text": "Медиа для профиля успешно сохранено в 'Избранное'!", "entity": MessageEntityBold}
        ])
    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌ ",}, 
            {"text": "Не удалось сохранить медиа:", "entity": MessageEntityBold},
            {"text": f"\n`{e}`"}
        ])

@register("setrepo", incoming=True)
async def set_repo_url(event):
    """Устанавливает URL репозитория для .info."""
    # ❗️ ИЗМЕНЕНО: с OWNER на TRUSTED
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [
            {"text": "🚫 "},
            {"text": "Доступ запрещен.", "entity": MessageEntityBold}
        ])

    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=1)
    
    if len(args) < 2 or not args[1].startswith("http"):
        return await build_and_edit(event, [
            {"text": "❌ "}, 
            {"text": f"Укажите полный URL. Пример: {prefix}setrepo https://github.com/username/repo", "entity": MessageEntityBold}
        ])
    
    url = args[1]
    db.set_setting("repo_url", url)
    await build_and_edit(event, [
        {"text": "✅ ",}, 
        {"text": "URL репозитория сохранен:", "entity": MessageEntityBold}, 
        {"text": f"\n`{url}`"}
    ])