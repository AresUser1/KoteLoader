# modules/admin.py
"""<manifest>
version: 1.0.1
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/admin.py
author: Kote

Команды:
• prefix [префикс] - Показать/изменить префикс
• restart - Перезапустить юзербота
• trust <id/ответ> - Добавить в доверенные
• untrust <id/ответ> - Удалить из доверенных
• db_stats - Статистика БД
• db_clear <модуль> - Очистить данные модуля
• db_backup - Создать бэкап БД
</manifest>"""

import os
import sys
import shutil
from pathlib import Path
from datetime import datetime
from core import register
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityCode, MessageEntityBold

# --- ПРЕМИУМ ЭМОДЗИ ---
SUCCESS_EMOJI_ID = 5255813619702049821
ROCKET_EMOJI_ID = 5445284980978621387
TRASH_EMOJI_ID = 5255831443816327915
CHART_EMOJI_ID = 5364265190353286344
WRENCH_EMOJI_ID = 5258023599419171861
ERROR_EMOJI_ID = 5985346521103604145
FOLDER_EMOJI_ID = 5877332341331857066
CLOCK_EMOJI_ID = 5778605968208170641

@register("prefix", incoming=True)
async def set_prefix(event):
    """Устанавливает или показывает префикс команд."""
    if not check_permission(event, min_level="TRUSTED"):
        return
        
    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=1)
    
    if len(args) < 2:
        await build_and_edit(event, [
            {"text": "Текущий префикс: "},
            {"text": f"{prefix}", "entity": MessageEntityCode},
            {"text": "\n\n"},
            {"text": "🔧", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": WRENCH_EMOJI_ID}},
            {"text": f" Для смены: {prefix}prefix <новый_префикс>", "entity": MessageEntityCode}
        ])
        return

    new_prefix = args[1]
    db.set_setting("prefix", new_prefix)
    await build_and_edit(event, [
        {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
        {"text": " Префикс изменен на ", "entity": MessageEntityBold},
        {"text": f"{new_prefix}", "entity": MessageEntityCode},
        {"text": f".\n\nЧтобы изменения вступили в силу, используйте команду {prefix}restart", "entity": MessageEntityCode}
    ])

@register("restart", incoming=True)
async def restart_bot(event):
    """Перезапускает юзербота с отчётом о статусе."""
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    db.set_setting("restart_report_chat_id", str(event.chat_id))
    
    await build_and_edit(event, [
        {"text": "🚀", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ROCKET_EMOJI_ID}},
        {"text": " Перезапускаюсь...", "entity": MessageEntityBold}
    ])
    os.execv(sys.executable, [sys.executable] + sys.argv)

@register("trust")
async def trust_user(event):
    """Добавляет пользователя в доверенные."""
    # ❗️ ОСТАВЛЕНО: Только OWNER может назначать TRUSTED
    if not check_permission(event, min_level="OWNER"):
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
        {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
        {"text": " Пользователь "},
        {"text": f"{user_id}", "entity": MessageEntityCode},
        {"text": " добавлен в доверенные."}
    ])

@register("untrust")
async def untrust_user(event):
    """Убирает пользователя из доверенных."""
    # ❗️ ОСТАВЛЕНО: Только OWNER может забирать права
    if not check_permission(event, min_level="OWNER"):
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
        {"text": "🗑", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": TRASH_EMOJI_ID}},
        {"text": " Пользователь "},
        {"text": f"{user_id}", "entity": MessageEntityCode},
        {"text": " удален из доверенных."}
    ])

@register("db_stats", incoming=True)
async def show_db_stats(event):
    """Показывает статистику использования БД модулями."""
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    try:
        stats = db.get_modules_stats()
        parts = []
        if not stats:
            return await build_and_edit(event, [
                {"text": "📊", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": CHART_EMOJI_ID}},
                {"text": " Статистика БД", "entity": MessageEntityBold},
                {"text": "\n\nНикакие модули еще не использовали базу данных."}
            ])

        parts.extend([
            {"text": "📊", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": CHART_EMOJI_ID}},
            {"text": " Статистика использования БД", "entity": MessageEntityBold},
            {"text": "\n\n"}
        ])
        
        total_configs, total_data = 0, 0
        for module, info in sorted(stats.items()):
            parts.extend([
                {"text": "🔧", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": WRENCH_EMOJI_ID}},
                {"text": f" {module}", "entity": MessageEntityBold},
                {"text": f":\n  • Настроек: {info['configs']}\n  • Данных: {info['data_entries']}\n"}
            ])
            if info['last_activity']:
                parts.append({"text": f"  • Активность: {info['last_activity'].split()[0]}\n"})
            parts.append({"text": "\n"})
            total_configs += info['configs']
            total_data += info['data_entries']

        parts.extend([
            {"text": "📊", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": CHART_EMOJI_ID}},
            {"text": " Итого", "entity": MessageEntityBold},
            {"text": f":\n• Модулей с данными: {len(stats)}\n• Всего настроек: {total_configs}\n• Всего записей данных: {total_data}"}
        ])
        await build_and_edit(event, parts)
        
    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Ошибка при получении статистики", "entity": MessageEntityBold},
            {"text": f":\n`{e}`"}
        ])

@register("db_clear", incoming=True)
async def clear_module_data(event):
    """Очищает все данные конкретного модуля из БД."""
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    prefix = db.get_setting("prefix", default=".")
    args = event.message.text.split(maxsplit=1)
    
    if len(args) < 2:
        modules_with_data = list(set(db.find_modules_with_configs() + db.find_modules_with_data()))
        
        parts = [
            {"text": "🗑", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": TRASH_EMOJI_ID}},
            {"text": " Очистка данных модуля", "entity": MessageEntityBold},
            {"text": "\n\n"}
        ]
        if not modules_with_data:
            parts.append({"text": "Нет модулей с данными в БД."})
        else:
            parts.append({"text": "Доступные модули для очистки:\n", "entity": MessageEntityBold})
            for module in sorted(modules_with_data):
                parts.append({"text": f"• "})
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
                {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
                {"text": " Модуль "},
                {"text": f"{module_name}", "entity": MessageEntityCode},
                {"text": " не имеет данных в БД."}
            ])
        
        db.clear_module(module_name)
        
        await build_and_edit(event, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
            {"text": " Все данные модуля ", "entity": MessageEntityBold},
            {"text": f"{module_name}", "entity": MessageEntityCode},
            {"text": " удалены из БД.", "entity": MessageEntityBold},
            {"text": f"\n\n• Настроек удалено: {len(configs)}\n• Записей данных удалено: {len(all_data)}"}
        ])
        
    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Ошибка при очистке данных", "entity": MessageEntityBold},
            {"text": f":\n`{e}`"}
        ])

@register("db_backup", incoming=True)
async def backup_database(event):
    """Создает резервную копию базы данных."""
    if not check_permission(event, min_level="TRUSTED"):
        return
    
    try:
        db_file = Path(__file__).parent.parent / "database.db"
        
        if not db_file.exists():
            return await build_and_edit(event, [
                {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
                {"text": " Файл базы данных не найден.", "entity": MessageEntityBold}
            ])
        
        backup_dir = Path(__file__).parent.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"database_backup_{timestamp}.db"
        
        shutil.copy2(db_file, backup_file)
        
        size_mb = round(backup_file.stat().st_size / 1024 / 1024, 2)
        
        await build_and_edit(event, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
            {"text": " Резервная копия БД создана!", "entity": MessageEntityBold},
            {"text": "\n\n"},
            {"text": "📁", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": FOLDER_EMOJI_ID}},
            {"text": " Файл: "},
            {"text": f"{backup_file.name}", "entity": MessageEntityCode},
            {"text": "\n"},
            {"text": "📊", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": CHART_EMOJI_ID}},
            {"text": f" Размер: {size_mb} MB\n"},
            {"text": "🕒", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": CLOCK_EMOJI_ID}},
            {"text": f" Время: {timestamp[:8]} {timestamp[9:11]}:{timestamp[11:13]}:{timestamp[13:15]}"}
        ])
        
    except Exception as e:
        await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_EMOJI_ID}},
            {"text": " Ошибка создания бэкапа", "entity": MessageEntityBold},
            {"text": f":\n`{e}`"}
        ])