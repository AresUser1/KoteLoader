# panels/global_menu.py

from telethon.tl.custom import Button
from telethon.tl.types import MessageEntityBold

def build_global_menu(as_text: bool = False):
    """
    Собирает меню глобальных действий.
    
    Args:
        as_text: Если True, возвращает обычный текст (для inline), иначе parts (для entities)
    """
    buttons = [
        [Button.inline("♻️ Перезагрузить все", data="reload:all")],
        [Button.inline("📤 Выгрузить все", data="unload:all")],
        [Button.inline("🔙 Назад в меню", data="back_to_main")]
    ]
    
    if as_text:
        # Для inline-запросов: обычный HTML текст
        text = "🌐 <b>Глобальные действия</b>\n\nВыберите действие, которое применится ко всем модулям."
        return text, buttons
    else:
        # Для обычных сообщений: parts с entities
        parts = []
        parts.append({"text": "🌐 "})
        parts.append({"text": "Глобальные действия", "entity": MessageEntityBold})
        parts.append({"text": "\n\nВыберите действие, которое применится ко всем модулям."})
        
        return parts, buttons