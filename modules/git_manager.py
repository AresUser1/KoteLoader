# modules/git_manager.py
"""<manifest>
version: 1.0.4
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/git_manager.py
author: Kote

Команды:
• set_gh_repo <url> - Установить URL репозитория
• set_gh_token <токен> - Установить Personal Access Token (PAT)
• upload_module <модуль> - Загрузить модуль в репозиторий
</manifest>"""

import aiohttp
import json
import re
import base64
from pathlib import Path

from core import register
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji

# --- Премиум Эмодзи ---
SUCCESS_EMOJI_ID = 5255813619702049821
ERROR_EMOJI_ID = 5985346521103604145
GIT_EMOJI_ID = 5968434789424832533
KEY_EMOJI_ID = 6005570495603282482
ROCKET_EMOJI_ID = 5445284980978621387

MODULES_DIR = Path(__file__).parent.parent / "modules"

def parse_repo_url(url: str) -> dict:
    """Извлекает 'owner' и 'repo' из URL-адреса GitHub."""
    match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
    if match:
        return {"owner": match.group(1), "repo": match.group(2).replace(".git", "")}
    return {}

def get_module_path(module_name: str) -> Path | None:
    """Находит полный путь к файлу модуля, включая вложенные."""
    potential_paths = list(MODULES_DIR.rglob(f"{module_name.replace('.', '/')}.py"))
    if potential_paths:
        return potential_paths[0]
    return None

def increment_version(version: str) -> str:
    """Увеличивает патч-версию (1.0.0 -> 1.0.1)"""
    parts = list(map(int, version.split('.')))
    parts[-1] += 1
    return ".".join(map(str, parts))

@register("set_gh_repo", incoming=True)
async def set_repo_alias(event):
    """Алиас для команды .setrepo, сохраняет URL репозитория."""
    if not check_permission(event, min_level="TRUSTED"):
        return

    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=1)
    
    if len(args) < 2 or not args[1].startswith("http"):
        return await build_and_edit(event, [{"text": f"❌ Укажите полный URL. Пример: {prefix}set_gh_repo https://github.com/username/repo", "entity": MessageEntityBold}])
    
    url = args[1]
    db.set_setting("repo_url", url)
    await build_and_edit(event, [
        {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
        {"text": " URL репозитория сохранен:", "entity": MessageEntityBold}, 
        {"text": f"\n`{url}`"}
    ])

@register("set_gh_token", incoming=True)
async def set_gh_token(event):
    """Сохраняет GitHub PAT в базу данных."""
    # ❗️❗️❗️ ИЗМЕНЕНИЕ: Убрано сообщение об ошибке. Теперь просто "return". ❗️❗️❗️
    if not check_permission(event, min_level="OWNER"):
        return

    token = (event.pattern_match.group(1) or "").strip()
    if not token.startswith("ghp_"):
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Неверный формат токена. Он должен начинаться с `ghp_`", "entity": MessageEntityBold}
        ])

    db.set_setting("github_token", token)
    await build_and_edit(event, [
        {"text": "🔑", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": KEY_EMOJI_ID}},
        {"text": " GitHub PAT токен сохранен в базу данных.", "entity": MessageEntityBold}
    ])
    await event.delete() # Удаляем сообщение с токеном из чата

@register("upload_module", incoming=True)
async def upload_module_cmd(event):
    """Автоматически обновляет версию и загружает модуль на GitHub."""
    if not check_permission(event, min_level="TRUSTED"):
        return

    module_name = (event.pattern_match.group(1) or "").strip()
    if not module_name:
        return await build_and_edit(event, [{"text": "❌ Укажите имя модуля."}])

    token = db.get_setting("github_token")
    repo_url = db.get_setting("repo_url")
    if not token or not repo_url:
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Сначала настройте репозиторий и токен:", "entity": MessageEntityBold},
            {"text": "\n`.set_gh_repo <url>`\n`.set_gh_token <token>`"}
        ])

    repo_info = parse_repo_url(repo_url)
    if not repo_info:
        return await build_and_edit(event, [{"text": "❌ Неверный URL репозитория в настройках."}])

    module_path = get_module_path(module_name)
    if not module_path:
        return await build_and_edit(event, [{"text": f"❌ Файл модуля `{module_name}` не найден."}])

    await build_and_edit(event, [
        {"text": "🚀", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ROCKET_EMOJI_ID}},
        {"text": f" Начинаю загрузку `{module_name}`... (1/4)", "entity": MessageEntityBold}
    ])

    # 1. Читаем и обновляем версию в манифесте
    try:
        content = module_path.read_text(encoding="utf-8")
        from services.module_info_cache import parse_manifest
        manifest = parse_manifest(content)
        
        if not manifest or "version" not in manifest:
            return await build_and_edit(event, [{"text": "❌ Не удалось найти манифест или версию в файле."}])

        old_version = manifest["version"]
        new_version = increment_version(old_version)
        content = content.replace(f"version: {old_version}", f"version: {new_version}")
        
        module_path.write_text(content, encoding="utf-8")
        
        await build_and_edit(event, [
            {"text": "🚀", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ROCKET_EMOJI_ID}},
            {"text": f" Версия обновлена: `{old_version}` → `{new_version}`. (2/4)", "entity": MessageEntityBold}
        ])
    except Exception as e:
        return await build_and_edit(event, [{"text": f"❌ Ошибка чтения/обновления файла: {e}"}])

    # 2. Подготовка к загрузке на GitHub
    owner, repo = repo_info["owner"], repo_info["repo"]
    file_path_in_repo = "modules/" + module_name.replace(".", "/") + ".py"
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path_in_repo}"
    
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        # 3. Получаем SHA файла (обязательно для обновления)
        await build_and_edit(event, [{"text": f"🚀 Получаю SHA файла... (3/4)", "entity": MessageEntityBold}])
        current_sha = None
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    current_sha = (await response.json()).get("sha")
                elif response.status != 404:
                    return await build_and_edit(event, [{"text": f"❌ Ошибка GitHub (GET): {response.status} {await response.text()}"}])

        # 4. Загружаем файл (создаем или обновляем)
        await build_and_edit(event, [{"text": f"🚀 Загружаю файл в репозиторий... (4/4)", "entity": MessageEntityBold}])
        
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
                        {"text": f" Ошибка загрузки на GitHub (PUT): {response.status}", "entity": MessageEntityBold},
                        {"text": f"\n`{await response.text()}`"}
                    ])
                
                commit_url = (await response.json())["commit"]["html_url"]

        await build_and_edit(event, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
            {"text": f" Модуль `{module_name}` успешно загружен!", "entity": MessageEntityBold},
            {"text": f"\nВерсия: `{new_version}`"},
            {"text": f"\nКоммит: {commit_url}"}
        ])

    except Exception as e:
        await build_and_edit(event, [{"text": f"❌ Критическая ошибка: {e}"}])