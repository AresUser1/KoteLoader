# modules/install.py
"""
<manifest>
version: 2.1.0
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/install.py
author: Kote
</manifest>

Управление модулями: установка, удаление, скачивание.
Включает защиту от даунгрейда системных модулей, проверку версии ядра
и АВТОМАТИЧЕСКУЮ установку зависимостей.
"""

import os
import sys
import ast
import aiohttp
import traceback
import asyncio
import shutil
import importlib.util
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional

from core import register
from utils import database as db
from utils.message_builder import build_and_edit, build_message
from utils.security import scan_code, check_permission
from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityBold, MessageEntityCode

def _pip_env() -> dict:
    """
    Возвращает окружение для pip с фиксом для Termux/Android.
    maturin (сборщик Rust-пакетов) падает если не задан ANDROID_API_LEVEL —
    это ломает установку cryptography и других Rust-зависимых пакетов.
    """
    env = os.environ.copy()
    if "ANDROID_API_LEVEL" not in env:
        # Android 7.0+ = API 24, безопасный минимум для большинства пакетов
        env["ANDROID_API_LEVEL"] = "24"
    return env
from services.module_info_cache import parse_manifest
from utils.loader import get_all_modules


def _is_heroku_module(content: str) -> bool:
    """
    Определяет Heroku/Hikka-совместимый модуль через AST-анализ импортов.
    Строковый поиск ненадёжен — install.py сам содержит эти строки в коде.
    """
    import ast as _ast
    try:
        tree = _ast.parse(content)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom):
                # from .. import loader  или  from ..xxx import yyy
                if node.level and node.level >= 1:
                    names = [a.name for a in node.names]
                    if "loader" in names or "utils" in names:
                        return True
                # from herokutl.xxx import yyy
                if node.module and "herokutl" in node.module:
                    return True
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    if "herokutl" in alias.name:
                        return True
    except Exception:
        pass
    return False

# --- Настройки ядра ---
CURRENT_CORE_VERSION = "2.0.0" # Версия текущей сборки

SUCCESS_EMOJI_ID = 5255813619702049821
FOLDER_EMOJI_ID = 5256113064821926998
TRASH_EMOJI_ID = 5255831443816327915
NOTE_EMOJI_ID = 5256230583717079814
PAW_EMOJI_ID = 5084923566848213749
SECURITY_INFO_ID = 5879785854284599288
SECURITY_BLOCK_ID = 5778527486270770928
SECURITY_WARN_ID = 5881702736843511327
LOCK_EMOJI_ID = 5778570255555105942
PIP_EMOJI_ID = 5364265190353286344 # Использую CHART для pip

MODULES_DIR = Path(__file__).parent.parent / "modules"

# --- Словарь зависимостей (Import -> Pip Package) ---
PIP_MAPPING = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "telethon": "telethon",
    "requests": "requests",
    "bs4": "beautifulsoup4",
    "google.generativeai": "google-generativeai",
    "google.ai.generativelanguage": "google-generativeai",
    "youtube_dl": "youtube_dl",
    "yt_dlp": "yt-dlp",
    "numpy": "numpy",
    "pandas": "pandas",
    "matplotlib": "matplotlib",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "dateutil": "python-dateutil",
    "pytz": "pytz",
    "aiohttp": "aiohttp",
    "colorama": "colorama",
    "emoji": "emoji",
    "qrcode": "qrcode",
    "gtts": "gTTS",
    "mutagen": "mutagen",
    "pydub": "pydub",
    "shazamio": "shazamio",
    "fuzzywuzzy": "fuzzywuzzy",
    "Levenshtein": "python-Levenshtein",
    "speedtest": "speedtest-cli",
    "translators": "translators",
    "deep_translator": "deep-translator",
    "git": "GitPython",
    "psutil": "psutil"
}

# Приблизительный список стандартных библиотек, чтобы не пытаться их ставить
STD_LIB = {
    "os", "sys", "math", "time", "datetime", "json", "re", "random", "asyncio", 
    "collections", "itertools", "functools", "typing", "pathlib", "shutil", 
    "logging", "traceback", "inspect", "importlib", "subprocess", "base64", 
    "hashlib", "io", "copy", "platform", "socket", "ssl", "urllib", "uuid",
    "ast", "pickle", "sqlite3", "html", "http", "email", "calendar", "zipfile",
    "gzip", "tarfile", "csv", "xml", "unittest", "tempfile", "weakref", "abc",
    # Модули стандартной библиотеки, которые могут быть удалены в Python 3.13+
    # или иметь __spec__ == None (что вызывает ValueError в find_spec)
    "imghdr", "sndhdr", "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt",
    "imaplib", "mailbox", "msilib", "nis", "nntplib", "ossaudiodev",
    "pipes", "pty", "readline", "resource", "spwd", "sunau", "telnetlib",
    "uu", "xdrlib", "contextlib", "dataclasses", "enum", "string", "struct",
    "binascii", "codecs", "unicodedata", "textwrap", "pprint", "reprlib",
    "numbers", "decimal", "fractions", "statistics", "cmath", "array",
    "queue", "heapq", "bisect", "types", "operator", "threading", "multiprocessing",
    "concurrent", "contextvars", "signal", "mmap", "ctypes", "gc", "builtins",
    "warnings", "contextlib", "atexit", "linecache", "tokenize", "keyword",
    "token", "symtable", "compileall", "dis", "marshal", "site", "code",
    "pdb", "profile", "timeit", "trace", "ftplib", "poplib", "smtplib",
    "imaplib", "xmlrpc", "ipaddress", "secrets", "hmac", "difflib", "fnmatch",
    "glob", "stat", "filecmp", "fileinput", "getpass", "getopt", "argparse",
    "configparser", "netrc", "plistlib", "cryptography"
}

def _find_module_path(user_input: str) -> Path | None:
    if not user_input: return None
    direct_path = MODULES_DIR / user_input
    if direct_path.exists(): return direct_path
    direct_path_py = direct_path.with_suffix(".py")
    if direct_path_py.exists(): return direct_path_py
    all_modules = get_all_modules()
    target_name = None
    user_input_clean = user_input.lower().replace("_", "")
    for mod in all_modules:
        if mod.lower() == user_input.lower():
            target_name = mod
            break
        if mod.lower().replace("_", "") == user_input_clean:
            target_name = mod
            break
    if target_name:
        parts = target_name.split(".")
        current = MODULES_DIR
        for part in parts[:-1]:
            current = current / part
        candidate_file = current / (parts[-1] + ".py")
        if candidate_file.exists(): return candidate_file
        candidate_dir = current / parts[-1]
        if candidate_dir.exists(): return candidate_dir
    return None

def compare_versions(ver1, ver2):
    """Возвращает True, если ver1 > ver2 (новая > старой)."""
    try:
        v1 = list(map(int, ver1.split('.')))
        v2 = list(map(int, ver2.split('.')))
        return v1 > v2
    except ValueError:
        return False

async def install_requirements(event, code_content: str):
    """Автоматически находит и устанавливает зависимости."""
    try:
        tree = ast.parse(code_content)
    except Exception:
        return # Если код не парсится, то и импорты не найдем

    needed_packages = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split('.')[0]
                if root_pkg not in STD_LIB:
                    needed_packages.add(root_pkg)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split('.')[0]
                if root_pkg not in STD_LIB:
                    needed_packages.add(root_pkg)

    packages_to_install = []
    
    for pkg in needed_packages:
        # 1. Проверяем, установлен ли модуль
        # find_spec может кинуть ValueError если __spec__ is None
        # (например, для удалённых модулей вроде imghdr в Python 3.13+)
        try:
            spec = importlib.util.find_spec(pkg)
        except (ValueError, ModuleNotFoundError):
            spec = None
        if spec is not None:
            continue
        
        # 2. Ищем имя пакета в маппинге
        # Если нет в маппинге, пробуем установить по имени импорта (часто совпадает)
        install_name = PIP_MAPPING.get(pkg, pkg)
        packages_to_install.append(install_name)

    if not packages_to_install:
        return

    # Удаляем дубликаты
    packages_to_install = list(set(packages_to_install))
    
    pkg_str = ", ".join([f"`{p}`" for p in packages_to_install])
    await build_and_edit(event, [
        {"text": "📦", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": PIP_EMOJI_ID}},
        {"text": " Устанавливаю зависимости: ", "entity": MessageEntityBold},
        {"text": f"{pkg_str}..."}
    ])

    for pkg in packages_to_install:
        try:
            process = await asyncio.create_subprocess_shell(
                f"{sys.executable} -m pip install {pkg}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_pip_env()
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                print(f"Failed to install {pkg}: {stderr.decode()}")
                # Не прерываемся, пробуем остальные
        except Exception as e:
            print(f"Error installing {pkg}: {e}")

async def _install_from_py_url(event, url, force=False):
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return await build_and_edit(event, f"**Ошибка скачивания: HTTP {response.status}**", parse_mode="md")
                content = await response.text(encoding='utf-8')
        
        file_name = os.path.basename(urlparse(url).path)
        await process_and_install(event, file_name, content, source_url=url, force=force)
    except Exception as e:
        await build_and_edit(event, f"**Критическая ошибка при установке:**\n`{e}`", parse_mode="md")

async def _install_from_git_repo(event, url, force=False):
    repo_name = url.split("/")[-1].replace(".git", "")
    target_dir = MODULES_DIR / repo_name
    
    if target_dir.exists() and not force:
        return await build_and_edit(event, [
            {"text": "⚠️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SECURITY_WARN_ID}},
            {"text": " Пакет модулей (папка) с таким именем уже существует.", "entity": MessageEntityBold}
        ])

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
        return await build_and_edit(event, f"❌ **Ошибка при клонировании:**\n`{error_message}`", parse_mode="md")

    await build_and_edit(event, "✅ **Репозиторий успешно склонирован.**", parse_mode="md")
    
    req_path = target_dir / "requirements.txt"
    if req_path.exists():
        await build_and_edit(event, "`requirements.txt`** найден, устанавливаю зависимости...", parse_mode="md")
        pip_process = await asyncio.create_subprocess_shell(
            f"pip install -r {req_path}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_pip_env()
        )
        pip_stdout, pip_stderr = await pip_process.communicate()

        if pip_process.returncode != 0:
            error_message = pip_stderr.decode().strip() or pip_stdout.decode().strip()
            return await build_and_edit(event, f"⚠️ **Ошибка при установке зависимостей:**\n`{error_message}`", parse_mode="md")

    found_modules = [p.stem for p in target_dir.rglob("*.py") if not p.name.startswith("_")]
    
    prefix = db.get_setting("prefix", default=".")
    if found_modules:
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
        await build_and_edit(event, f"⚠️ **Пакет `{repo_name}` установлен, но в нем не найдено исполняемых .py модулей.**", parse_mode="md")

async def process_and_install(event, file_name, content, source_url=None, force=False):
    prefix = db.get_setting("prefix", default=".")
    module_name = file_name[:-3]
    module_path = MODULES_DIR / file_name
    
    # Парсим новый манифест сразу
    new_manifest = parse_manifest(content)
    
    # 1. ПРОВЕРКА ВЕРСИИ ЯДРА (Min Core Version)
    min_core = new_manifest.get("min_core")
    if min_core:
        if compare_versions(min_core, CURRENT_CORE_VERSION):
            return await build_and_edit(event, [
                {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SECURITY_BLOCK_ID}},
                {"text": " Ошибка совместимости!\n", "entity": MessageEntityBold},
                {"text": f"Модуль требует ядро версии "},
                {"text": f"{min_core}", "entity": MessageEntityCode},
                {"text": f", а у вас "},
                {"text": f"{CURRENT_CORE_VERSION}", "entity": MessageEntityCode},
                {"text": ". Обновите бота."}
            ])

    # Импортируем список защищенных модулей
    try:
        from modules.modules import PROTECTED_MODULES
    except ImportError:
        PROTECTED_MODULES = []

    # 2. ПРОВЕРКА ВЕРСИЙ МОДУЛЯ (Anti-Rollback)
    version_msg = ""
    if module_path.exists():
        try:
            with open(module_path, 'r', encoding='utf-8') as f:
                current_content = f.read()
            
            current_manifest = parse_manifest(current_content)
            
            curr_ver = current_manifest.get("version", "0.0.0")
            new_ver = new_manifest.get("version", "0.0.0")
            
            # Если модуль защищенный и версия НИЖЕ текущей - БЛОКИРУЕМ
            if module_name in PROTECTED_MODULES:
                if compare_versions(curr_ver, new_ver): # curr > new
                     return await build_and_edit(event, [
                        {"text": "🔒", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": LOCK_EMOJI_ID}},
                        {"text": " Откат запрещен!\n", "entity": MessageEntityBold},
                        {"text": f"Вы пытаетесь установить старую версию ({new_ver}) системного модуля поверх новой ({curr_ver}). Это действие заблокировано."}
                    ])
            
            if compare_versions(new_ver, curr_ver):
                force = True # Авто-апдейт разрешен, если версия выше
                version_msg = f" (обновлено: {curr_ver} → {new_ver})"
            elif not force:
                return await build_and_edit(event, [
                    {"text": "⚠️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SECURITY_WARN_ID}},
                    {"text": f" Модуль существует (v{curr_ver}). Новая версия ({new_ver}) не новее.\n", "entity": MessageEntityBold},
                    {"text": f"Используйте {prefix}forceupload."}
                ])
        except Exception as e:
            if not force:
                return await build_and_edit(event, [
                    {"text": "⚠️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SECURITY_WARN_ID}},
                    {"text": f" Модуль существует. Ошибка проверки: {e}.\n", "entity": MessageEntityBold},
                    {"text": f"Используйте {prefix}forceupload."}
                ])

    # Heroku/Hikka-модули имеют свои паттерны (__import__, aiohttp, getattr) — норма
    is_heroku = _is_heroku_module(content)

    if not force and not is_heroku:
        await build_and_edit(event, [
            {"text": "🛡️ "}, 
            {"text": "Анализирую код на безопасность...", "entity": MessageEntityBold}
        ])
        
        scan_result = scan_code(content)
        level = scan_result["level"]

        # Игнорируем уровень INFO
        if level != "safe" and level != "info":
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
    elif not force and is_heroku:
        await build_and_edit(event, [
            {"text": "ℹ️ "},
            {"text": "Heroku-модуль — сканер безопасности пропущен.", "entity": MessageEntityBold}
        ])
    
    # --- SMART DEPENDENCY INSTALLER ---
    await install_requirements(event, content)
    # ----------------------------------

    with open(module_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    if source_url:
        db.set_module_config(module_name, "source_url", source_url) 
    else:
        db.remove_module_config(module_name, "source_url")

    if _is_heroku_module(content):
        try:
            from compat.heroku_loader import load_heroku_module
            result = await load_heroku_module(event.client, module_path)
            if result["status"] == "ok":
                cmds = result["commands"]
                cmds_str = ", ".join([f"{prefix}{c}" for c in cmds]) if cmds else "—"
                await build_and_edit(event, [
                    {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
                    {"text": " Heroku-модуль ", "entity": MessageEntityBold},
                    {"text": f"{module_name}", "entity": MessageEntityCode},
                    {"text": f" загружен{version_msg}!", "entity": MessageEntityBold},
                    {"text": "\n\n"},
                    {"text": "📝", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": NOTE_EMOJI_ID}},
                    {"text": " Команды: "},
                    {"text": cmds_str, "entity": MessageEntityCode},
                ])
            else:
                await build_and_edit(event, [
                    {"text": "⚠️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SECURITY_WARN_ID}},
                    {"text": " Установлен, авто-загрузка не удалась:\n", "entity": MessageEntityBold},
                    {"text": result["message"][:300], "entity": MessageEntityCode},
                    {"text": "\n\nЗагрузи вручную: "},
                    {"text": f"{prefix}load {module_name}", "entity": MessageEntityCode},
                ])
        except Exception as _heroku_e:
            await build_and_edit(event, [
                {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
                {"text": " Модуль ", "entity": MessageEntityBold},
                {"text": f"{module_name}", "entity": MessageEntityCode},
                {"text": f" установлен{version_msg}!", "entity": MessageEntityBold},
                {"text": "\n\n"},
                {"text": "📝", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": NOTE_EMOJI_ID}},
                {"text": " Для загрузки используй: "},
                {"text": f"{prefix}load {module_name}", "entity": MessageEntityCode},
            ])
    else:
        await build_and_edit(event, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
            {"text": " Модуль ", "entity": MessageEntityBold},
            {"text": f"{module_name}", "entity": MessageEntityCode},
            {"text": f" успешно установлен{version_msg}!", "entity": MessageEntityBold},
            {"text": "\n\n"},
            {"text": "📝", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": NOTE_EMOJI_ID}},
            {"text": " Для загрузки используй: "},
            {"text": f"{prefix}load {module_name}", "entity": MessageEntityCode}
        ])

@register("install", incoming=True)
async def install_cmd(event, force=False):
    """Установить модуль по ссылке.
    Usage: {prefix}install <url>"""
    if not check_permission(event, min_level="TRUSTED"): return
    prefix = db.get_setting("prefix", default=".")
    url = (event.pattern_match.group(1) or "").strip()
    if not url.startswith("http"): return await build_and_edit(event, f"❌ **Укажите полный URL. Использование: {prefix}install <url>**", parse_mode="md")
    if url.endswith(".py"): await _install_from_py_url(event, url, force)
    elif "github.com" in url: await _install_from_git_repo(event, url, force)
    else: await build_and_edit(event, f"**Ссылка не распознана. Использование: {prefix}install <url>**", parse_mode="md")

@register("forceinstall", incoming=True)
async def force_install_cmd(event):
    """Принудительная установка по ссылке.
    Usage: {prefix}forceinstall <url>"""
    await install_cmd(event, force=True)

@register("upload", incoming=True)
async def upload_module(event, force=False):
    """Установка модуля из файла.
    Usage: {prefix}upload (в ответ на файл)"""
    if not check_permission(event, min_level="TRUSTED"): return
    reply = await event.get_reply_message()
    message_with_file = reply if reply and reply.media else event.message
    if not message_with_file or not message_with_file.file: return await build_and_edit(event, "**Отправьте .py файл или ответьте на него командой.**", parse_mode="md")
    file_name = getattr(message_with_file.file, 'name', "module.py")
    if not file_name.endswith(".py"): return await build_and_edit(event, "**Файл должен быть .py**", parse_mode="md")
    await build_and_edit(event, "🔄 **Читаю файл...**", parse_mode="md")
    content = (await message_with_file.download_media(bytes)).decode('utf-8', 'ignore')
    await process_and_install(event, file_name, content, force=force)

@register("forceupload", incoming=True)
async def force_upload_module(event):
    """Принудительная установка из файла.
    Usage: {prefix}forceupload (в ответ на файл)"""
    await upload_module(event, force=True)

@register("getm", incoming=True)
async def get_module_cmd(event):
    """Получить файл модуля.
    Usage: {prefix}getm <название>"""
    if not check_permission(event, min_level="TRUSTED"): return
    module_name = event.pattern_match.group(1)
    if not module_name: return await build_and_edit(event, "**Укажите имя модуля.**", parse_mode="md")

    # ❗️ ЗАЩИТА ОТ КОПИРОВАНИЯ ❗️
    from modules.modules import PROTECTED_MODULES
    if module_name.lower() in PROTECTED_MODULES:
        return await build_and_edit(event, [
            {"text": "🔒", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": LOCK_EMOJI_ID}},
            {"text": " Ошибка: ", "entity": MessageEntityBold},
            {"text": f"Модуль ", "entity": MessageEntityBold},
            {"text": module_name, "entity": MessageEntityCode},
            {"text": " защищен от копирования.", "entity": MessageEntityBold}
        ])

    module_path = _find_module_path(module_name)
    if not module_path: return await build_and_edit(event, f"❌ **Модуль `{module_name}` не найден.**", parse_mode="md")
    prefix = db.get_setting("prefix", default=".")
    parts = [{"text": "📁", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": FOLDER_EMOJI_ID}}, {"text": " Файл модуля ", "entity": MessageEntityBold}, {"text": f"{module_path.name}", "entity": MessageEntityCode}, {"text": "\n\n"}, {"text": "🐾", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": PAW_EMOJI_ID}}, {"text": " "}, {"text": f"{prefix}upload", "entity": MessageEntityCode}, {"text": " в ответ на это сообщение для быстрой установки", "entity": MessageEntityBold}]
    caption, entities = build_message(parts)
    await event.client.send_file(event.chat_id, file=module_path, caption=caption, formatting_entities=entities, reply_to=event.id)
    if event.out: await event.delete()

@register("delm", incoming=True)
async def remove_module(event):
    """Удалить модуль.
    Usage: {prefix}delm <название>"""
    if not check_permission(event, min_level="TRUSTED"): return
    name_to_remove = (event.pattern_match.group(1) or "").strip()
    if not name_to_remove: return await build_and_edit(event, "**Укажите имя модуля или пакета для удаления.**", parse_mode="md")
    
    path_to_remove = _find_module_path(name_to_remove)
    if not path_to_remove: return await build_and_edit(event, f"❌ **Ресурс `{name_to_remove}` не найден.**", parse_mode="md")
    
    # ❗️ ЗАЩИТА ОТ УДАЛЕНИЯ ❗️
    from modules.modules import PROTECTED_MODULES
    module_clean_name = path_to_remove.stem
    if module_clean_name in PROTECTED_MODULES:
         return await build_and_edit(event, [
            {"text": "🔒", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": LOCK_EMOJI_ID}},
            {"text": " Ошибка: ", "entity": MessageEntityBold},
            {"text": f"Модуль ", "entity": MessageEntityBold},
            {"text": module_clean_name, "entity": MessageEntityCode},
            {"text": " защищен от удаления.", "entity": MessageEntityBold}
        ])

    try:
        if path_to_remove.is_dir():
            shutil.rmtree(path_to_remove)
            all_module_names_in_db = db.get_modules_stats().keys()
            for mod_name in all_module_names_in_db:
                if mod_name.startswith(name_to_remove + "."): db.clear_module(mod_name)
        else:
            from utils.loader import unload_module
            try:
                rel_path = path_to_remove.relative_to(MODULES_DIR)
                module_name = ".".join(rel_path.with_suffix("").parts)
            except ValueError: module_name = path_to_remove.stem
            if hasattr(event.client, 'modules') and module_name in event.client.modules: await unload_module(event.client, module_name)
            path_to_remove.unlink()
            db.clear_module(module_name)
        await build_and_edit(event, f"✅ **Ресурс `{path_to_remove.name}` успешно удален!**", parse_mode="md")
    except Exception as e: await build_and_edit(event, f"❌ **Ошибка при удалении:**\n`{traceback.format_exc()}`", parse_mode="md")
