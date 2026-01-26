# modules/git_manager.py
"""
<manifest>
version: 1.0.7
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/git_manager.py
author: Kote
</manifest>

Инструмент для разработчиков модулей.
Позволяет автоматически обновлять версию модуля и загружать его в ваш GitHub репозиторий.
"""

import aiohttp
import json
import re
import base64
from pathlib import Path
from typing import Optional

from core import register
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji

SUCCESS_EMOJI_ID = 5255813619702049821
ERROR_EMOJI_ID = 5985346521103604145
GIT_EMOJI_ID = 5968434789424832533
KEY_EMOJI_ID = 6005570495603282482
ROCKET_EMOJI_ID = 5445284980978621387

MODULES_DIR = Path(__file__).parent.parent / "modules"

def parse_repo_url(url: str) -> dict:
    match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
    if match:
        return {"owner": match.group(1), "repo": match.group(2).replace(".git", "")}
    return {}

def get_module_path(module_name: str) -> Path | None:
    # Делаем поиск регистронезависимым
    module_name_lower = module_name.lower().replace('.', '/') + ".py"
    for path in MODULES_DIR.rglob("*.py"):
        relative_path = path.relative_to(MODULES_DIR).as_posix().lower()
        if relative_path == module_name_lower:
            return path
    return None

def increment_version(version: str) -> str:
    parts = list(map(int, version.split('.')))
    parts[-1] += 1
    return ".".join(map(str, parts))

@register("set_gh_repo", incoming=True)
async def set_repo_alias(event):
    """Установить URL репозитория.
    
    Usage: {prefix}set_gh_repo <url>
    """
    if not check_permission(event, min_level="TRUSTED"):
        return

    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=1)
    
    if len(args) < 2 or not args[1].startswith("http"):
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Укажите полный URL. Пример: "},
            {"text": f"{prefix}set_gh_repo https://github.com/username/repo", "entity": MessageEntityCode}
        ])
    
    url = args[1]
    db.set_setting("repo_url", url)
    await build_and_edit(event, [
        {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
        {"text": " URL репозитория сохранен:", "entity": MessageEntityBold}, 
        {"text": f"\n"},
        {"text": url, "entity": MessageEntityCode}
    ])

@register("set_gh_token", incoming=True)
async def set_gh_token(event):
    """Установить GitHub Token (PAT).
    
    Usage: {prefix}set_gh_token <token>
    """
    if not check_permission(event, min_level="OWNER"):
        return

    token = (event.pattern_match.group(1) or "").strip()
    if not token.startswith("ghp_"):
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Неверный формат токена. Он должен начинаться с ", "entity": MessageEntityBold},
            {"text": "ghp_", "entity": MessageEntityCode}
        ])

    db.set_setting("github_token", token)
    await build_and_edit(event, [
        {"text": "🔑", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": KEY_EMOJI_ID}},
        {"text": " GitHub PAT токен сохранен в базу данных.", "entity": MessageEntityBold}
    ])
    await event.delete() 

@register("upload_module", incoming=True)
async def upload_module_cmd(event):
    """Загрузить модуль на GitHub.
    
    Usage: {prefix}upload_module <модуль>
    """
    if not check_permission(event, min_level="TRUSTED"):
        return

    module_name = (event.pattern_match.group(1) or "").strip()
    if not module_name:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Укажите имя модуля.", "entity": MessageEntityBold}
        ])

    token = db.get_setting("github_token")
    repo_url = db.get_setting("repo_url")
    if not token or not repo_url:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Сначала настройте репозиторий и токен:", "entity": MessageEntityBold},
            {"text": "\n"},
            {"text": ".set_gh_repo <url>", "entity": MessageEntityCode},
            {"text": "\n"},
            {"text": ".set_gh_token <token>", "entity": MessageEntityCode}
        ])

    repo_info = parse_repo_url(repo_url)
    if not repo_info:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Неверный URL репозитория в настройках.", "entity": MessageEntityBold}
        ])

    module_path = get_module_path(module_name)
    if not module_path:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Файл модуля "},
            {"text": module_name, "entity": MessageEntityCode},
            {"text": " не найден.", "entity": MessageEntityBold}
        ])

    await build_and_edit(event, [
        {"text": "🚀", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ROCKET_EMOJI_ID}},
        {"text": " Начинаю загрузку ", "entity": MessageEntityBold},
        {"text": module_name, "entity": MessageEntityCode},
        {"text": "... (1/4)", "entity": MessageEntityBold}
    ])

    try:
        content = module_path.read_text(encoding="utf-8")
        from services.module_info_cache import parse_manifest
        manifest = parse_manifest(content)
        
        if not manifest or "version" not in manifest:
            return await build_and_edit(event, [
                {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
                {"text": " Не удалось найти манифест или версию в файле.", "entity": MessageEntityBold}
            ])

        old_version = manifest["version"]
        new_version = increment_version(old_version)
        content = content.replace(f"version: {old_version}", f"version: {new_version}")
        
        module_path.write_text(content, encoding="utf-8")
        
        await build_and_edit(event, [
            {"text": "🚀", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ROCKET_EMOJI_ID}},
            {"text": " Версия обновлена: ", "entity": MessageEntityBold},
            {"text": old_version, "entity": MessageEntityCode},
            {"text": " → "},
            {"text": new_version, "entity": MessageEntityCode},
            {"text": ". (2/4)", "entity": MessageEntityBold}
        ])
    except Exception as e:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": f" Ошибка чтения/обновления файла: {e}", "entity": MessageEntityBold}
        ])

    owner, repo = repo_info["owner"], repo_info["repo"]
    file_path_in_repo = "modules/" + module_name.lower().replace(".", "/") + ".py"
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path_in_repo}"
    
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        await build_and_edit(event, [{"text": "🚀 Получаю SHA файла... (3/4)", "entity": MessageEntityBold}])
        current_sha = None
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    current_sha = (await response.json()).get("sha")
                elif response.status != 404:
                    return await build_and_edit(event, [
                        {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
                        {"text": f" Ошибка GitHub (GET): {response.status}", "entity": MessageEntityBold}
                    ])

        await build_and_edit(event, [{"text": "🚀 Загружаю файл в репозиторий... (4/4)", "entity": MessageEntityBold}])
        
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        commit_message = f"🚀 Обновление модуля {module_name} до v{new_version}"
        
        data = {
            "message": commit_message,
            "content": content_b64,
            "branch": "main" 
        }
        if current_sha:
            data["sha"] = current_sha 

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.put(api_url, json=data) as response:
                if response.status not in [200, 201]: 
                    return await build_and_edit(event, [
                        {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
                        {"text": f" Ошибка загрузки на GitHub (PUT): {response.status}", "entity": MessageEntityBold}
                    ])
                
                commit_url = (await response.json())["commit"]["html_url"]

        await build_and_edit(event, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
            {"text": " Модуль ", "entity": MessageEntityBold},
            {"text": module_name, "entity": MessageEntityCode},
            {"text": " успешно загружен!", "entity": MessageEntityBold},
            {"text": "\nВерсия: "},
            {"text": new_version, "entity": MessageEntityCode},
            {"text": "\nКоммит: "},
            {"text": commit_url, "entity": MessageEntityCode}
        ])

    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": f" Критическая ошибка: {e}", "entity": MessageEntityBold}
        ])