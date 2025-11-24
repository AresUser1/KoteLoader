# modules/updater.py
"""
<manifest>
version: 1.0.6
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/updater.py
author: Kote
</manifest>

Модуль для проверки и установки обновлений модулей.
Сканирует локальные файлы, сверяет версии с манифестом в репозитории и обновляет их.
"""

import aiohttp
import time
import traceback
from pathlib import Path

from core import register
from utils.loader import reload_module
from utils.security import check_permission
from handlers.user_commands import _call_inline_bot
from services.module_info_cache import parse_manifest

MODULES_DIR = Path(__file__).parent.parent / "modules"

async def check_for_updates():
    """
    Сканирует все модули на наличие обновлений.
    Возвращает список словарей с информацией о найденных обновлениях.
    """
    updates_to_do = []
    for module_file in MODULES_DIR.rglob("*.py"):
        if any(part.startswith('.') for part in module_file.parts) or '__pycache__' in module_file.parts:
            continue

        try:
            with open(module_file, "r", encoding="utf-8") as f:
                content = f.read()
                local_manifest = parse_manifest(content)
            
            if not local_manifest or "source" not in local_manifest or "version" not in local_manifest:
                continue
                
            source_url = local_manifest["source"]
            if not source_url or source_url == "local": continue
            
            url_to_fetch = f"{source_url}?t={int(time.time())}"
            headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}

            async with aiohttp.ClientSession(trust_env=False) as session:
                async with session.get(url_to_fetch, headers=headers) as response:
                    if response.status != 200: continue
                    remote_content = await response.text()
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
    """Запускает проверку обновлений через инлайн-меню.
    
    Usage: .check_updates
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    try:
        await _call_inline_bot(event, "updates:check")
    except Exception as e:
        await event.respond(f"**❌ Не удалось вызвать меню обновлений.**\n**Ошибка:** `{e}`")

@register("update", incoming=True)
async def update_cmd(event):
    """Команда, которую использует бот для установки обновления.
    
    Usage: .update <название> [chat_id]
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    args = (event.pattern_match.group(1) or "").strip().split()
    if not args:
        return
    
    module_to_update = args[0]
    report_chat_id = event.chat_id
    
    if len(args) > 1:
        try:
            report_chat_id = int(args[1])
        except ValueError:
            pass

    try:
        message = await event.client.send_message(report_chat_id, f"**Обновляю `{module_to_update}`...**", parse_mode="md")
    except Exception as e:
        await event.respond(f"**Не удалось отправить отчет об обновлении {module_to_update}.**\n`{e}`", parse_mode="md")
        return
    
    updates = await check_for_updates()
    found = next((u for u in updates if u["module_name"] == module_to_update), None)
    
    if not found:
        return await message.edit(f"**ℹ️ Обновление для `{module_to_update}` не найдено.**", parse_mode="md")
        
    try:
        url_to_fetch = f"{found['source']}?t={int(time.time())}"
        headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
        
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.get(url_to_fetch, headers=headers) as response:
                remote_content = await response.text()
        
        with open(Path(found["file_path"]), "w", encoding="utf-8") as f:
            f.write(remote_content)
        
        await reload_module(event.client, found["module_name"])
        await message.edit(f"✅ **Модуль `{found['module_name']}` обновлен до версии {found['new_version']}!**", parse_mode="md")
        
    except Exception:
        await message.edit(f"**❌ Ошибка при обновлении `{module_to_update}`:**\n`{traceback.format_exc()}`", parse_mode="md")