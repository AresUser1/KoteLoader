# panels/module_menu.py

from telethon.tl.custom import Button
from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityItalic
from services.module_info_cache import get_module_info

def build_module_menu(module_name: str, as_text: bool = False):
    """
    Собирает подменю для конкретного модуля.
    
    Args:
        module_name: Имя модуля
        as_text: Если True, возвращает обычный текст (для inline), иначе parts (для entities)
    """
    info = get_module_info(module_name)
    
    buttons = [
        [
            Button.inline("♻️ Перезагрузить", data=f"reload:{module_name}"),
            Button.inline("📤 Выгрузить", data=f"unload:{module_name}")
        ],
        [Button.inline("✅ Загрузить", data=f"load:{module_name}")],
        [Button.inline("🔙 Назад в меню", data="back_to_main")]
    ]
    
    if as_text:
        # Для inline-запросов: обычный HTML текст
        text = f"<b>Модуль:</b> <code>{module_name}</code>\n\n<i>ℹ️ {info}</i>"
        return text, buttons
    else:
        # Для обычных сообщений: parts с entities
        parts = []
        parts.append({"text": "Модуль: ", "entity": MessageEntityBold})
        parts.append({"text": module_name, "entity": MessageEntityCode})
        parts.append({"text": "\n\n"})
        parts.append({"text": "ℹ️ ", "entity": MessageEntityItalic})
        parts.append({"text": info, "entity": MessageEntityItalic})
        
        return parts, buttons