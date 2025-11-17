# modules/install.py
"""<manifest>
version: 1.0.5
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/install.py
author: Kote

Команды:
• install <url> - Установить модуль по ссылке
• forceinstall <url> - Принудительно установить
• upload - Установить из файла
• forceupload - Принудительно установить из файла
• remove <название> - Удалить модуль
• getm <название> - Получить файл модуля
</manifest>"""

import os
import aiohttp
import traceback
import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlparse

from core import register
from utils import database as db
from utils.message_builder import build_and_edit, build_message
from utils.security import scan_code, check_permission
from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityBold, MessageEntityCode

# --- ПРЕМИУМ ЭМОДЗИ ---
SUCCESS_EMOJI_ID = 5255813619702049821
FOLDER_EMOJI_ID = 5256113064821926998
TRASH_EMOJI_ID = 5255831443816327915
NOTE_EMOJI_ID = 5256230583717079814
PAW_EMOJI_ID = 5084923566848213749
SECURITY_INFO_ID = 5879785854284599288
SECURITY_BLOCK_ID = 5778527486270770928
SECURITY_WARN_ID = 5881702736843511327

MODULES_DIR = Path(__file__).parent.parent / "modules"

async def _install_from_py_url(event, url, force=False):
    """Логика для установки из прямой ссылки на .py файл."""
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
                    return await build_and_edit(event, f"**Ошибка скачивания: HTTP {response.status}**", parse_mode="md")
                content = await response.text(encoding='utf-8')
        
        file_name = os.path.basename(urlparse(url).path)
        await process_and_install(event, file_name, content, source_url=url, force=force)
    except Exception as e:
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        await build_and_edit(event, f"**Критическая ошибка при установке:**\n`{e}`", parse_mode="md")

async def _install_from_git_repo(event, url, force=False):
    """Логика для установки из GitHub репозитория."""
    repo_name = url.split("/")[-1].replace(".git", "")
    target_dir = MODULES_DIR / repo_name
    
    if target_dir.exists() and not force:
        # Эта функция уже использует 'parts' (премиум-эмодзи), она была в порядке.
        return await build_and_edit(event, [
            {"text": "⚠️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SECURITY_WARN_ID}},
            {"text": " Пакет модулей (папка) с таким именем уже существует.", "entity": MessageEntityBold}
        ])

    # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
    await build_and_edit(event, f"⚙️ **Начинаю клонирование репозитория `{repo_name}`...**", parse_mode="md")
    
    if target_dir.exists():
        shutil.rmtree(target_dir)

    process = await asyncio.create_subprocess_shell(
        f"git clone --depth 1 {url} {target_dir}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_message = stderr.decode().strip() or stdout.decode().strip()
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        return await build_and_edit(event, f"❌ **Ошибка при клонировании:**\n`{error_message}`", parse_mode="md")

    # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
    await build_and_edit(event, "✅ **Репозиторий успешно склонирован.**", parse_mode="md")
    
    req_path = target_dir / "requirements.txt"
    if req_path.exists():
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        await build_and_edit(event, "`requirements.txt`** найден, устанавливаю зависимости...**", parse_mode="md")
        pip_process = await asyncio.create_subprocess_shell(
            f"pip install -r {req_path}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        pip_stdout, pip_stderr = await pip_process.communicate()

        if pip_process.returncode != 0:
            error_message = pip_stderr.decode().strip() or pip_stdout.decode().strip()
            # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
            return await build_and_edit(event, f"⚠️ **Ошибка при установке зависимостей:**\n`{error_message}`", parse_mode="md")

    found_modules = [p.stem for p in target_dir.rglob("*.py") if not p.name.startswith("_")]
    
    prefix = db.get_setting("prefix", default=".")
    if found_modules:
        # Эта функция уже использует 'parts' (премиум-эмодзи), она была в порядке.
        await build_and_edit(event, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
            {"text": " Пакет модулей ", "entity": MessageEntityBold},
            {"text": f"{repo_name}", "entity": MessageEntityCode},
            {"text": " успешно установлен!\n\n", "entity": MessageEntityBold},
            {"text": "📝", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": NOTE_EMOJI_ID}},
            {"text": " Для загрузки первого модуля используйте: "}, 
            {"text": f"{prefix}load {repo_name}.{found_modules[0]}", "entity": MessageEntityCode}
        ])
    else:
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        await build_and_edit(event, f"⚠️ **Пакет `{repo_name}` установлен, но в нем не найдено исполняемых .py модулей.**", parse_mode="md")

async def process_and_install(event, file_name, content, source_url=None, force=False):
    """Общая логика для проверки и установки ОДИНОЧНОГО модуля."""
    prefix = db.get_setting("prefix", default=".")
    
    if not force:
        # Эта функция использует 'parts' и премиум-эмодзи, она была в порядке.
        await build_and_edit(event, [
            {"text": "🛡️ "}, 
            {"text": "Анализирую код на безопасность...", "entity": MessageEntityBold}
        ])
        
        scan_result = scan_code(content)
        level = scan_result["level"]

        if level != "safe":
            emoji_map = {
                "block": {"emoji": "❌", "id": SECURITY_BLOCK_ID, "title": "Установка отменена. Обнаружены критические угрозы:"},
                "warning": {"emoji": "⚠️", "id": SECURITY_WARN_ID, "title": "Обнаружены потенциальные угрозы:"},
                "info": {"emoji": "ℹ️", "id": SECURITY_INFO_ID, "title": "Информация о модуле:"}
            }
            report_info = emoji_map.get(level)
            parts = [
                {"text": report_info["emoji"], "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": report_info["id"]}},
                {"text": f" {report_info['title']}", "entity": MessageEntityBold},
                {"text": "\n\n"}
            ]
            for reason in scan_result["reasons"]:
                text_part, code_part = reason.rsplit(":", 1)
                code_part = code_part.strip().strip('`')
                parts.extend([
                    {"text": report_info["emoji"], "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": report_info["id"]}},
                    {"text": f" {text_part}: "},
                    {"text": code_part, "entity": MessageEntityCode},
                    {"text": "\n"}
                ])
            if level != "block":
                cmd = f"{prefix}forceinstall" if source_url else f"{prefix}forceupload"
                parts.extend([
                    {"text": "\nМодуль может быть небезопасным. Если вы доверяете источнику, используйте команду "},
                    {"text": cmd, "entity": MessageEntityCode},
                    {"text": " для принудительной установки."}
                ])
            else:
                 parts.append({"text": "\nЭтот модуль не будет установлен."})
            return await build_and_edit(event, parts)

    module_name = file_name[:-3]
    module_path = MODULES_DIR / file_name
    
    if module_path.exists() and not force:
        # Эта функция использует 'parts' и премиум-эмодзи, она была в порядке.
        return await build_and_edit(event, [
            {"text": "⚠️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SECURITY_WARN_ID}},
            {"text": " Модуль уже существует.", "entity": MessageEntityBold}
        ])

    with open(module_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    if source_url:
        db.set_module_config(module_name, "source_url", source_url) 
    else:
        db.remove_module_config(module_name, "source_url")

    # Эта функция использует 'parts' и премиум-эмодзи, она была в порядке.
    await build_and_edit(event, [
        {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
        {"text": " Модуль ", "entity": MessageEntityBold},
        {"text": f"{module_name}", "entity": MessageEntityCode},
        {"text": " успешно установлен!", "entity": MessageEntityBold},
        {"text": "\n\n"},
        {"text": "📝", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": NOTE_EMOJI_ID}},
        {"text": " Для загрузки используй: "}, 
        {"text": f"{prefix}load {module_name}", "entity": MessageEntityCode}
    ])

@register("install", incoming=True)
async def install_cmd(event, force=False):
    """Главный обработчик команды install."""
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    prefix = db.get_setting("prefix", default=".")
    url = (event.pattern_match.group(1) or "").strip()
    
    if not url.startswith("http"):
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        return await build_and_edit(event, f"❌ **Укажите полный URL. Использование: {prefix}install <url>**", parse_mode="md")

    if url.endswith(".py"):
        await _install_from_py_url(event, url, force)
    elif "github.com" in url:
        await _install_from_git_repo(event, url, force)
    else:
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        await build_and_edit(event, f"**Ссылка не распознана. Использование: {prefix}install <url>**", parse_mode="md")

@register("forceinstall", incoming=True)
async def force_install_cmd(event):
    """Принудительная установка без проверки безопасности."""
    await install_cmd(event, force=True)

@register("upload", incoming=True)
async def upload_module(event, force=False):
    """Установка модуля из присланного файла."""
    if not check_permission(event, min_level="TRUSTED"):
        return

    reply = await event.get_reply_message()
    message_with_file = reply if reply and reply.media else event.message
    
    if not message_with_file or not message_with_file.file:
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown (ВАШ ПРИМЕР)
        return await build_and_edit(event, "**Отправьте .py файл или ответьте на него командой.**", parse_mode="md")

    file_name = getattr(message_with_file.file, 'name', "module.py")
    if not file_name.endswith(".py"): 
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        return await build_and_edit(event, "**Файл должен быть .py**", parse_mode="md")

    # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
    await build_and_edit(event, "🔄 **Читаю файл...**", parse_mode="md")
    
    content = (await message_with_file.download_media(bytes)).decode('utf-8', 'ignore')
    await process_and_install(event, file_name, content, force=force)

@register("forceupload", incoming=True)
async def force_upload_module(event):
    """Принудительная установка из файла без проверки безопасности."""
    await upload_module(event, force=True)

@register("getm", incoming=True)
async def get_module_cmd(event):
    """Отправляет файл модуля в чат."""
    if not check_permission(event, min_level="TRUSTED"):
        return

    module_name = event.pattern_match.group(1)
    if not module_name:
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        return await build_and_edit(event, "**Укажите имя модуля.**", parse_mode="md")

    module_path = None
    potential_paths = list(MODULES_DIR.rglob(f"{module_name.replace('.', '/')}.py"))
    if potential_paths:
        module_path = potential_paths[0]

    if not module_path or not module_path.exists():
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        return await build_and_edit(event, f"❌ **Модуль `{module_name}` не найден.**", parse_mode="md")

    prefix = db.get_setting("prefix", default=".")
    
    # Эта функция использует 'parts' и премиум-эмодзи, она была в порядке.
    parts = [
        {"text": "📁", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": FOLDER_EMOJI_ID}},
        {"text": " Файл модуля ", "entity": MessageEntityBold},
        {"text": f"{module_name}", "entity": MessageEntityCode},
        {"text": "\n\n"},
        {"text": "🐾", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": PAW_EMOJI_ID}},
        {"text": " "},
        {"text": f"{prefix}upload", "entity": MessageEntityCode},
        {"text": " в ответ на это сообщение для быстрой установки", "entity": MessageEntityBold},
    ]
    caption, entities = build_message(parts)

    await event.client.send_file(
        event.chat_id,
        file=module_path,
        caption=caption,
        formatting_entities=entities,
        reply_to=event.id
    )
    
    if event.out:
        await event.delete()

@register("remove", incoming=True)
async def remove_module(event):
    """Удаляет модуль (файл) или пакет модулей (папку)."""
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    name_to_remove = (event.pattern_match.group(1) or "").strip()
    if not name_to_remove:
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        return await build_and_edit(event, "**Укажите имя модуля или пакета для удаления.**", parse_mode="md")

    path_to_remove = MODULES_DIR / name_to_remove.replace(".", os.sep)
    if not path_to_remove.exists():
        path_to_remove = (MODULES_DIR / name_to_remove.replace(".", os.sep)).with_suffix(".py")

    if not path_to_remove.exists():
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        return await build_and_edit(event, f"❌ **Ресурс `{name_to_remove}` не найден.**", parse_mode="md")
    
    try:
        if path_to_remove.is_dir():
            shutil.rmtree(path_to_remove)
            # При удалении пакета (папки) нужно очистить из БД все модули ВНУТРИ него.
            all_module_names_in_db = db.get_modules_stats().keys()
            for mod_name in all_module_names_in_db:
                if mod_name.startswith(name_to_remove + "."):
                    db.clear_module(mod_name)
        else:
            # Это одиночный файл.
            from utils.loader import unload_module
            module_name = ".".join(path_to_remove.relative_to(MODULES_DIR).with_suffix("").parts)
            if hasattr(event.client, 'modules') and module_name in event.client.modules:
                await unload_module(event.client, module_name)
            path_to_remove.unlink()
            db.clear_module(module_name)
            
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        await build_and_edit(event, f"✅ **Ресурс `{name_to_remove}` успешно удален!**", parse_mode="md")
        
    except Exception as e:
        # ❗️ ИСПРАВЛЕНИЕ: Конвертировано в Markdown
        await build_and_edit(event, f"❌ **Ошибка при удалении:**\n`{traceback.format_exc()}`", parse_mode="md")