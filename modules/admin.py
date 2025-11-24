# modules/admin.py
"""
<manifest>
version: 1.1.2
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/admin.py
author: Kote
</manifest>

Модуль для управления ядром бота,доступом и базой данных.
"""

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
from utils.message_builder import build_and_edit, utf16len
from utils.security import check_permission
from handlers.user_commands import _call_inline_bot
from telethon.tl.types import (
    MessageEntityCode, MessageEntityBold, MessageEntityTextUrl, 
    MessageEntityBlockquote, MessageEntityItalic
)
from telethon.tl.custom import Button

MODULES_DIR = Path(__file__).parent.parent / "modules"

@register("prefix", incoming=True)
async def set_prefix(event):
    """Показать или изменить префикс.
    
    Usage: {prefix}prefix [новый_знак]
    """
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
    """Мгновенная перезагрузка юзербота.
    
    Usage: {prefix}restart
    """
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    try:
        await build_and_edit(event, [
            {"text": "🚀 Перезапускаюсь...", "entity": MessageEntityBold}
        ])
    except Exception as e:
        print(f"Не удалось отправить сообщение о перезапуске: {e}")
    
    if event.out:
        db.set_setting("restart_report_chat_id", str(event.chat_id))
        db.set_setting("restart_start_time", str(time.time()))
    
    os.execv(sys.executable, [sys.executable] + sys.argv)


@register("trust", incoming=True)
async def trust_user(event):
    """Добавить пользователя в доверенные.
    
    Usage: {prefix}trust <id/ответ>
    """
    if not check_permission(event, min_level="OWNER"):
        return

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
    """Удалить пользователя из доверенных.
    
    Usage: {prefix}untrust <id/ответ>
    """
    if not check_permission(event, min_level="OWNER"):
        return

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

@register("listtrust", incoming=True)
async def list_trusted_users(event):
    """Показать список доверенных лиц.
    
    Usage: {prefix}listtrust
    """
    if not check_permission(event, min_level="TRUSTED"):
        return

    text_parts = []
    entities = []
    current_offset = 0

    def append_part(text, entity_type=None, **kwargs):
        nonlocal current_offset
        text_parts.append(text)
        if entity_type:
            length = utf16len(text)
            if length > 0:
                entities.append(entity_type(offset=current_offset, length=length, **kwargs))
        current_offset += utf16len(text)

    try:
        owner_ids = db.get_users_by_level("OWNER")
        trusted_ids = db.get_users_by_level("TRUSTED")
        
        owner_only_ids = owner_ids
        trusted_only_ids = [uid for uid in trusted_ids if uid not in owner_ids] 

        if owner_only_ids:
            quote_start_offset = current_offset
            append_part("👑 Владелец:", MessageEntityBold)
            append_part("\n")
            
            for owner_id in owner_only_ids:
                try:
                    entity = await event.client.get_entity(owner_id)
                    name = entity.first_name or f"User {owner_id}"
                    append_part("• ")
                    append_part(name, MessageEntityTextUrl, url=f"tg://user?id={owner_id}")
                    append_part(f" (ID: {owner_id})\n")
                except Exception:
                    append_part("• ")
                    append_part(f"Не удалось найти ID: {owner_id}\n", MessageEntityItalic)
            
            if text_parts[-1].endswith('\n'):
                text_parts[-1] = text_parts[-1][:-1]
                current_offset -= utf16len('\n')

            quote_length = current_offset - quote_start_offset
            if quote_length > 0:
                entities.append(MessageEntityBlockquote(offset=quote_start_offset, length=quote_length, collapsed=True))
            
            append_part("\n")

        if trusted_only_ids:
            if owner_only_ids:
                append_part("\n")

            quote_start_offset = current_offset
            append_part("👥 Доверенные пользователи:", MessageEntityBold)
            append_part("\n")

            for user_id in trusted_only_ids:
                try:
                    entity = await event.client.get_entity(user_id)
                    name = entity.first_name or f"User {user_id}"
                    append_part("• ")
                    append_part(name, MessageEntityTextUrl, url=f"tg://user?id={user_id}")
                    append_part(f" (ID: {user_id})\n")
                except Exception:
                    append_part("• ")
                    append_part(f"Не удалось найти ID: {user_id}\n", MessageEntityItalic)

            if text_parts[-1].endswith('\n'):
                text_parts[-1] = text_parts[-1][:-1]
                current_offset -= utf16len('\n')

            quote_length = current_offset - quote_start_offset
            if quote_length > 0:
                entities.append(MessageEntityBlockquote(offset=quote_start_offset, length=quote_length, collapsed=True))
            
            append_part("\n")

        final_text = "".join(text_parts).strip()
        if not final_text:
            return await build_and_edit(event, [{"text": "ℹ️ Список доступа пуст.", "entity": MessageEntityItalic}])
        
        await event.edit(final_text, formatting_entities=entities, link_preview=False)
        
    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌ Ошибка при получении списка:", "entity": MessageEntityBold},
            {"text": f"\n`{e}`"}
        ])


@register("db_stats", incoming=True)
async def show_db_stats(event):
    """Показать статистику использования БД.
    
    Usage: {prefix}db_stats
    """
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
    """Очистить данные модуля из БД.
    
    Usage: {prefix}db_clear <модуль>
    """
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
    """Создать бэкап базы данных.
    
    Usage: {prefix}db_backup
    """
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
    """Создать ZIP-архив всех модулей.
    
    Usage: {prefix}backup_modules
    """
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