# modules/core_updater.py
"""
<manifest>
version: 1.3.1
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/core_updater.py
author: Kote
</manifest>

Модуль для полного обновления ядра KoteLoader из Git.
Перезаписывает все локальные изменения в системных файлах.
"""

import asyncio
import subprocess
import traceback
import time
import os
import sys
from core import register
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityBold, MessageEntityCode

@register("updatecore", incoming=True)
async def update_core_cmd(event):
    """Принудительно обновляет ядро бота из Git и перезагружается.
    
    Usage: {prefix}updatecore [confirm]
    """
    if not check_permission(event, min_level="OWNER"):
        return

    repo_url = "https://github.com/AresUser1/KoteLoader" 

    prefix = db.get_setting("prefix", default=".")
    args = (event.pattern_match.group(1) or "").strip()

    if args != "confirm":
        return await build_and_edit(event, [
            {"text": "⚠️"},
            {"text": " ВНИМАНИЕ!", "entity": MessageEntityBold},
            {"text": "\n\nЭта команда полностью перезапишет все отслеживаемые (core) файлы последней версией из Git. "},
            {"text": "Все несохраненные изменения в ядре будут потеряны.", "entity": MessageEntityBold},
            {"text": "\n\nВаши данные (БД, конфиг, сессия, user-модули) "},
            {"text": "не будут затронуты.", "entity": MessageEntityBold},
            {"text": f"\n\nДля подтверждения, введите: "},
            {"text": f"{prefix}updatecore confirm", "entity": MessageEntityCode}
        ])

    def _run_git(cmd: list) -> tuple[int, str]:
        """Синхронный запуск git-команды (совместимо с Python 3.12)."""
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore'
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode, output

    try:
        await build_and_edit(event, [
            {"text": "⚙️"},
            {"text": " Начинаю обновление ядра...", "entity": MessageEntityBold},
            {"text": "\n(1/3) Получаю данные (git fetch)..."}
        ])

        loop = asyncio.get_event_loop()
        rc_f, out_f = await loop.run_in_executor(
            None, _run_git, ["git", "fetch", repo_url]
        )

        if rc_f != 0:
            return await build_and_edit(event, [
                {"text": "❌"},
                {"text": " Ошибка 'git fetch':", "entity": MessageEntityBold},
                {"text": f"\n{out_f}", "entity": MessageEntityCode}
            ])

        await build_and_edit(event, [
             {"text": "⚙️"},
             {"text": " Обновление ядра...", "entity": MessageEntityBold},
             {"text": "\n(2/3) Перезаписываю файлы (git reset --hard FETCH_HEAD)..."}
        ])

        rc_r, reset_output = await loop.run_in_executor(
            None, _run_git, ["git", "reset", "--hard", "FETCH_HEAD"]
        )

        if rc_r != 0:
            return await build_and_edit(event, [
                {"text": "❌"},
                {"text": " Ошибка 'git reset':", "entity": MessageEntityBold},
                {"text": f"\n{reset_output}", "entity": MessageEntityCode}
            ])

        await build_and_edit(event, [
            {"text": "✅"},
            {"text": " Ядро успешно обновлено!", "entity": MessageEntityBold},
            {"text": f"\n\n"},
            {"text": reset_output, "entity": MessageEntityCode},
            {"text": f"\n\n"},
            {"text": "🚀"},
            {"text": " Перезагружаюсь для применения изменений...", "entity": MessageEntityBold},
            {"text": "\n(3/3)"}
        ])
        
        db.set_setting("restart_report_chat_id", str(event.chat_id))
        db.set_setting("restart_start_time", str(time.time()))
        
        os.execv(sys.executable, [sys.executable] + sys.argv)
        
    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌"},
            {"text": " Критическая ошибка во время обновления:", "entity": MessageEntityBold},
            {"text": f"\n{traceback.format_exc()}", "entity": MessageEntityCode}
        ])