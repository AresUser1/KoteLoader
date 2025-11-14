# modules/admin.py
"""<manifest>
version: 1.0.7
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/admin.py
author: Kote

Команды:
• prefix [префикс] - Показать или изменить префикс юзербота.
• restart - Мгновенная перезагрузка юзербота.
• trust <id/ответ> - Добавить пользователя в список доверенных лиц.
• untrust <id/ответ> - Удалить пользователя из списка доверенных лиц.
• db_stats - Показать статистику использования базы данных по модулям.
• db_clear <модуль> - Очистить все данные (настройки и записи) указанного модуля.
• db_backup - Создать и отправить файл бэкапа базы данных в чат.
• backup_modules - Создать ZIP-архив всех модулей и отправить его в чат.
</manifest>"""

import os
import sys
import shutil
import zipfile
import asyncio
import time
from pathlib import Path
from datetime import datetime
from core import register, inline_handler, callback_handler
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission
from handlers.user_commands import _call_inline_bot
from telethon.tl.types import MessageEntityCode, MessageEntityBold
from telethon.tl.custom import Button

MODULES_DIR = Path(__file__).parent.parent / "modules"

@register("prefix", incoming=True)
async def set_prefix(event):
    """Показать или изменить префикс юзербота."""
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=1)
    
    if len(args) < 2:
        await build_and_edit(event, [
            {"text": "Текущий префикс: "},
            {"text": f"{prefix}", "entity": MessageEntityCode},
            {"text": "\n\n"},
            {"text": f"🔧 Для смены: {prefix}prefix <новый_префикс>", "entity": MessageEntityCode}
        ])
        return

    new_prefix = args[1]
    db.set_setting("prefix", new_prefix)
    await build_and_edit(event, [
        {"text": "✅"},
        {"text": " Префикс изменен на ", "entity": MessageEntityBold},
        {"text": f"{new_prefix}", "entity": MessageEntityCode},
        {"text": f".\n\nЧтобы изменения вступили в силу, используйте команду {prefix}restart", "entity": MessageEntityCode}
    ])

@register("restart", incoming=True)
async def restart_bot(event):
    """Выполняет реальную перезагрузку."""
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    try:
        await build_and_edit(event, [
            {"text": "🚀 Перезапускаюсь...", "entity": MessageEntityBold}
        ])
    except Exception as e:
        print(f"Не удалось отправить сообщение о перезапуске: {e}")
    
    # ❗️❗️❗️ ИЗМЕНЕНИЕ: Сохраняем chat_id, ТОЛЬКО если это исходящая команда (от пользователя) ❗️❗️❗️
    # Если команда входящая (от .updatecore), мы ПОЛАГАЕМСЯ на то, 
    # что .updatecore УЖЕ установил правильный chat_id.
    if event.out:
        db.set_setting("restart_report_chat_id", str(event.chat_id))
        db.set_setting("restart_start_time", str(time.time()))
    
    os.execv(sys.executable, [sys.executable] + sys.argv)


@register("trust", incoming=True)
async def trust_user(event):
    """Добавить пользователя в список доверенных лиц."""
    if not check_permission(event, min_level="OWNER"):
        if db.get_user_level(event.sender_id) != "OWNER":
            return
        return await build_and_edit(event, [
            {"text": "🚫 "}, 
            {"text": "Только владелец может использовать эту команду.", "entity": MessageEntityBold}
        ])

    prefix = db.get_setting("prefix", default=".")
    
    try:
        user_id = int(event.message.text.split(maxsplit=1)[1])
    except (ValueError, IndexError):
        reply = await event.get_reply_message()
        if not reply:
            return await build_and_edit(event, [
                {"text": "❌ "},
                {"text": f"Укажите ID пользователя или ответьте на его сообщение. Использование: {prefix}trust <id>", "entity": MessageEntityBold}
            ])
        user_id = reply.sender_id
        
    db.add_user(user_id, "TRUSTED")
    await build_and_edit(event, [
        {"text": "✅"},
        {"text": " Пользователь "},
        {"text": f"{user_id}", "entity": MessageEntityCode},
        {"text": " добавлен в доверенные."}
    ])

@register("untrust", incoming=True)
async def untrust_user(event):
    """Удалить пользователя из списка доверенных лиц."""
    if not check_permission(event, min_level="OWNER"):
        if db.get_user_level(event.sender_id) != "OWNER":
            return
        return await build_and_edit(event, [
            {"text": "🚫 "}, 
            {"text": "Только владелец может использовать эту команду.", "entity": MessageEntityBold}
        ])

    prefix = db.get_setting("prefix", default=".")
    
    try:
        user_id = int(event.message.text.split(maxsplit=1)[1])
    except (ValueError, IndexError):
        reply = await event.get_reply_message()
        if not reply:
            return await build_and_edit(event, [
                {"text": "❌ "},
                {"text": f"Укажите ID пользователя или ответьте на его сообщение. Использование: {prefix}untrust <id>", "entity": MessageEntityBold}
            ])
        user_id = reply.sender_id
        
    if db.get_user_level(user_id) == "OWNER":
        return await build_and_edit(event, [
            {"text": "❌ "},
            {"text": "Нельзя лишить доступа владельца.", "entity": MessageEntityBold}
        ])

    db.remove_user(user_id)
    await build_and_edit(event, [
        {"text": "🗑"},
        {"text": " Пользователь "},
        {"text": f"{user_id}", "entity": MessageEntityCode},
        {"text": " удален из доверенных."}
    ])

@register("db_stats", incoming=True)
async def show_db_stats(event):
    """Показать статистику использования базы данных по модулям."""
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    try:
        stats = db.get_modules_stats()
        parts = []
        if not stats:
            return await build_and_edit(event, [
                {"text": "📊"},
                {"text": " Статистика БД", "entity": MessageEntityBold},
                {"text": "\n\nНикакие модули еще не использовали базу данных."}
            ])

        parts.extend([
            {"text": "📊"},
            {"text": " Статистика использования БД", "entity": MessageEntityBold},
            {"text": "\n\n"}
        ])
        
        total_configs, total_data = 0, 0
        for module, info in sorted(stats.items()):
            parts.extend([
                {"text": "🔧"},
                {"text": f" {module}", "entity": MessageEntityBold},
                {"text": f":\n  • Настроек: {info['configs']}\n  • Данных: {info['data_entries']}\n"}
            ])
            if info['last_activity']:
                parts.append({"text": f"  • Активность: {info['last_activity'].split()[0]}\n"})
            parts.append({"text": "\n"})
            total_configs += info['configs']
            total_data += info['data_entries']

        parts.extend([
            {"text": "📊"},
            {"text": " Итого", "entity": MessageEntityBold},
            {"text": f":\n• Модулей с данными: {len(stats)}\n• Всего настроек: {total_configs}\n• Всего записей данных: {total_data}"}
        ])
        await build_and_edit(event, parts)
        
    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌"},
            {"text": " Ошибка при получении статистики", "entity": MessageEntityBold},
            {"text": f":\n`{e}`"}
        ])

@register("db_clear", incoming=True)
async def clear_module_data(event):
    """Очистить все данные (настройки и записи) указанного модуля."""
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=1)
    
    if len(args) < 2:
        stats = db.get_modules_stats()
        modules_with_data = sorted(stats.keys())
        
        parts = [
            {"text": "🗑"},
            {"text": " Очистка данных модуля", "entity": MessageEntityBold},
            {"text": "\n\n"}
        ]
        if not modules_with_data:
            parts.append({"text": "Нет модулей с данными в БД."})
        else:
            parts.append({"text": "Доступные модули для очистки:\n", "entity": MessageEntityBold})
            for module in modules_with_data:
                parts.append({"text": "• "})
                parts.append({"text": f"{module}", "entity": MessageEntityCode})
                parts.append({"text": "\n"})
            parts.append({"text": "\nИспользование: ", "entity": MessageEntityBold})
            parts.append({"text": f"{prefix}db_clear <module_name>", "entity": MessageEntityCode})
        
        return await build_and_edit(event, parts)
    
    module_name = args[1]
    
    try:
        configs = db.get_all_module_configs(module_name)
        all_data = db.get_all_module_data(module_name)
        
        if not configs and not all_data:
            return await build_and_edit(event, [
                {"text": "❌"},
                {"text": " Модуль "},
                {"text": f"{module_name}", "entity": MessageEntityCode},
                {"text": " не имеет данных в БД."}
            ])
        
        db.clear_module(module_name)
        
        await build_and_edit(event, [
            {"text": "✅"},
            {"text": " Все данные модуля ", "entity": MessageEntityBold},
            {"text": f"{module_name}", "entity": MessageEntityCode},
            {"text": " удалены из БД.", "entity": MessageEntityBold},
            {"text": f"\n\n• Настроек удалено: {len(configs)}\n• Записей данных удалено: {len(all_data)}"}
        ])
        
    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌"},
            {"text": " Ошибка при очистке данных", "entity": MessageEntityBold},
            {"text": f":\n`{e}`"}
        ])

@register("db_backup", incoming=True)
async def backup_database(event):
    """Создать и отправить файл бэкапа базы данных в чат."""
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    try:
        db_file = Path(__file__).parent.parent / "database.db"
        
        if not db_file.exists():
            return await build_and_edit(event, [
                {"text": "❌"},
                {"text": " Файл базы данных не найден.", "entity": MessageEntityBold}
            ])
        
        await event.client.send_file(
            event.chat_id,
            db_file,
            caption=f"✅ <b>Резервная копия БД</b>\n<code>database.db</code>",
            parse_mode="html"
        )
        
        if event.out:
            await event.delete()
        
    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌"},
            {"text": " Ошибка создания бэкапа", "entity": MessageEntityBold},
            {"text": f":\n`{e}`"}
        ])

@register("backup_modules", incoming=True)
async def backup_modules_cmd(event):
    """Создать ZIP-архив всех модулей и отправить его в чат."""
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"modules_backup_{timestamp}.zip"
    
    try:
        await build_and_edit(event, [
            {"text": "🗜️"},
            {"text": " Начинаю архивацию модулей... Это может занять время.", "entity": MessageEntityBold}
        ])

        def create_zip():
            """Синхронная функция для создания zip-архива"""
            with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in MODULES_DIR.rglob("*"):
                    if "__pycache__" in file_path.parts or ".git" in file_path.parts:
                        continue
                    zipf.write(file_path, file_path.relative_to(MODULES_DIR.parent))

        await asyncio.to_thread(create_zip)

        await event.client.send_file(
            event.chat_id,
            zip_filename,
            caption=f"✅ <b>Резервная копия всех модулей</b>\n<code>{zip_filename}</code>",
            parse_mode="html"
        )
        
        await event.delete()

    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌"},
            {"text": " Ошибка при архивации модулей", "entity": MessageEntityBold},
            {"text": f":\n`{e}`"}
        ])
    finally:
        if os.path.exists(zip_filename):
            os.remove(zip_filename)