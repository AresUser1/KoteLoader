# modules/updater.py
"""
Модуль для проверки и установки обновлений для других модулей через интерактивное меню.

Команды:
• check_updates - Проверить обновления и показать меню в боте.
• update <название> - Установить обновление (используется ботом).
"""

import aiohttp
import json
import re
import traceback
from pathlib import Path
import pickle
import base64
import time

from core import register
from utils.loader import reload_module
# ❗️ ДОБАВЛЕНЫ ИМПОРТЫ
from utils.security import check_permission
from utils.message_builder import build_and_edit
from telethon.tl.types import MessageEntityBold

MODULES_DIR = Path(__file__).parent.parent / "modules"

# ❗️ ИЗМЕНЕНИЕ: Используем парсер из `module_info_cache`, чтобы не дублировать код
from services.module_info_cache import parse_manifest

async def check_for_updates():
    """
    Сканирует все модули на наличие обновлений.
    Возвращает список словарей с информацией о найденных обновлениях.
    """
    updates_to_do = []
    for module_file in MODULES_DIR.rglob("*.py"):
        # ❗️ УЛУЧШЕНИЕ: Пропускаем .git, __pycache__ и любые папки, начинающиеся с .
        if any(part.startswith('.') for part in module_file.parts) or '__pycache__' in module_file.parts:
            continue

        try:
            with open(module_file, "r", encoding="utf-8") as f:
                content = f.read()
                # ❗️ ИСПОЛЬЗУЕМ ОБЩИЙ ПАРСЕР
                local_manifest = parse_manifest(content)
            
            if not local_manifest or "source" not in local_manifest or "version" not in local_manifest:
                continue
                
            source_url = local_manifest["source"]
            # ❗️ ИСПОЛЬЗУЕМ ОБЩИЙ ПАРСЕР
            if not source_url: continue # Пропускаем, если нет source
            
            url_to_fetch = f"{source_url}?t={int(time.time())}"
            headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}

            async with aiohttp.ClientSession() as session:
                async with session.get(url_to_fetch, headers=headers) as response:
                    if response.status != 200: continue
                    remote_content = await response.text()
                    # ❗️ ИСПОЛЬЗУЕМ ОБЩИЙ ПАРСЕР
                    remote_manifest = parse_manifest(remote_content)
            
            if not remote_manifest or "version" not in remote_manifest: continue
            
            local_v = tuple(map(int, local_manifest["version"].split('.')))
            remote_v = tuple(map(int, remote_manifest["version"].split('.')))

            if remote_v > local_v:
                updates_to_do.append({
                    "file_path": str(module_file),
                    "module_name": ".".join(module_file.relative_to(MODULES_DIR).with_suffix("").parts),
                    "old_version": local_manifest["version"],
                    "new_version": remote_manifest["version"],
                    "source": local_manifest["source"]
                })
        except Exception:
            continue
            
    return updates_to_do

@register("check_updates", incoming=True)
async def check_updates_cmd(event):
    """Запускает проверку обновлений через инлайн-меню."""
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        return await build_and_edit(event, [{"text": "🚫 "}, {"text": "Доступ запрещен.", "entity": MessageEntityBold}])
        
    # Изменено: отправляем новый ответ вместо редактирования, чтобы избежать конфликтов
    message = await event.respond("🔎 **Запрашиваю меню обновлений...**")
    try:
        bot = event.client.bot_client
        me = await bot.get_me()
        # "Просим" инлайн-бота показать меню, не передавая никаких данных
        results = await event.client.inline_query(me.username, "updates:check")
        await results[0].click(event.chat_id)
        # Удаляем наш временный ответ, а не исходную команду
        await message.delete()
    except Exception as e:
        await message.edit(f"**❌ Не удалось вызвать меню обновлений.**\n\n"
                           f"**Возможная причина:** ваш инлайн-бот выключен или не настроен.\n"
                           f"**Ошибка:** `{e}`")

@register("update", incoming=True)
async def update_cmd(event):
    """
    Команда, которую будет вызывать бот для фактического обновления.
    Usage: <название_модуля>
    """
    # ❗️ ДОБАВЛЕНА ПРОВЕРКА
    if not check_permission(event, min_level="TRUSTED"):
        # (Эта команда обычно вызывается ботом, но проверка не помешает)
        return
        
    module_to_update = (event.pattern_match.group(1) or "").strip()
    if not module_to_update: return
    
    message = await event.edit(f"**Обновляю `{module_to_update}`...**")
    
    updates = await check_for_updates()
    found = next((u for u in updates if u["module_name"] == module_to_update), None)
    
    if not found:
        return await message.edit(f"**ℹ️ Обновление для `{module_to_update}` не найдено.**")
        
    try:
        url_to_fetch = f"{found['source']}?t={int(time.time())}"
        headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
        async with aiohttp.ClientSession() as session:
            async with session.get(url_to_fetch, headers=headers) as response:
                remote_content = await response.text()
        
        with open(Path(found["file_path"]), "w", encoding="utf-8") as f:
            f.write(remote_content)
        
        await reload_module(event.client, found["module_name"])
        
        await message.edit(f"✅ **Модуль `{found['module_name']}` обновлен до версии {found['new_version']}!**")
        
    except Exception:
        await message.edit(f"**❌ Ошибка при обновлении `{module_to_update}`:**\n`{traceback.format_exc()}`")