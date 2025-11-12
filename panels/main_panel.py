# panels/main_panel.py

from telethon.tl.custom import Button
from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityItalic
from utils.loader import get_all_modules
from services.state_manager import get_loaded_modules

def build_main_panel(page: int = 0, search_query: str = None, as_text: bool = False):
    """
    Собирает главное меню со списком модулей.
    
    Args:
        page: Номер страницы
        search_query: Поисковый запрос
        as_text: Если True, возвращает обычный текст (для inline), иначе parts (для entities)
    """
    loaded_modules = get_loaded_modules()
    all_modules = sorted(get_all_modules())
    
    if search_query:
        all_modules = [mod for mod in all_modules if search_query.lower() in mod.lower()]
    
    per_page = 8
    total_items = len(all_modules)
    total_pages = (total_items + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    modules_to_show = all_modules[start:end]

    # Создаём кнопки
    buttons = []
    row = []
    for i, module in enumerate(modules_to_show):
        status_emoji = "✅" if module in loaded_modules else "❌"
        row.append(Button.inline(f"{status_emoji} {module}", data=f"module:{module}"))
        if (i + 1) % 2 == 0:
            buttons.append(row)
            row = []
    if row: 
        buttons.append(row)

    # Навигация
    nav_row = []
    if page > 0: 
        nav_row.append(Button.inline("⬅️ Назад", data=f"page:{page - 1}"))
    if end < total_items: 
        nav_row.append(Button.inline("Вперёд ➡️", data=f"page:{page + 1}"))
    if nav_row: 
        buttons.append(nav_row)
        
    buttons.append([Button.inline("🌐 Глобальные действия", data="global_menu")])
    buttons.append([Button.inline("🔄 Обновить", data="refresh")])
    
    # Выбираем формат возврата
    if as_text:
        # Для inline-запросов: обычный HTML текст
        text = "<b>Панель управления KoteLoader</b>\n\n"
        if search_query:
            text += f"🔍 Результаты поиска: <b>{search_query}</b>\n\n"
        text += f"✅ Загружено: {len(loaded_modules)} из {total_items} модулей.\n"
        if total_pages > 1:
            text += f"📄 Страница: {page + 1}/{total_pages}\n"
        return text, buttons
    else:
        # Для обычных сообщений: parts с entities
        parts = []
        parts.append({"text": "Панель управления KoteLoader", "entity": MessageEntityBold})
        parts.append({"text": "\n\n"})
        
        if search_query:
            parts.append({"text": "🔍 Результаты поиска: "})
            parts.append({"text": search_query, "entity": MessageEntityBold})
            parts.append({"text": "\n\n"})
        
        parts.append({"text": f"✅ Загружено: {len(loaded_modules)} из {total_items} модулей.\n"})
        if total_pages > 1:
            parts.append({"text": f"📄 Страница: {page + 1}/{total_pages}\n"})
        
        return parts, buttons


def build_module_detail_panel(module_name: str, description: str = None, as_text: bool = False):
    """
    Собирает панель детальной информации о модуле.
    
    Args:
        module_name: Имя модуля
        description: Описание модуля
        as_text: Если True, возвращает обычный текст (для inline), иначе parts (для entities)
    """
    loaded_modules = get_loaded_modules()
    is_loaded = module_name in loaded_modules
    
    # Создаём кнопки
    buttons = []
    if is_loaded:
        buttons.append([Button.inline("❌ Выгрузить", data=f"unload:{module_name}")])
    else:
        buttons.append([Button.inline("✅ Загрузить", data=f"load:{module_name}")])
    
    buttons.append([Button.inline("🔙 Назад", data="back_to_main")])
    
    if as_text:
        # Для inline-запросов: обычный HTML текст
        text = f"<b>Модуль:</b> <code>{module_name}</code>\n\n"
        if description:
            text += f"<i>ℹ️ {description}</i>"
        else:
            text += "<i>ℹ️ Описание отсутствует.</i>"
        return text, buttons
    else:
        # Для обычных сообщений: parts с entities
        parts = []
        parts.append({"text": "Модуль: ", "entity": MessageEntityBold})
        parts.append({"text": module_name, "entity": MessageEntityCode})
        parts.append({"text": "\n\n"})
        
        if description:
            parts.append({"text": "ℹ️ ", "entity": MessageEntityItalic})
            parts.append({"text": description, "entity": MessageEntityItalic})
        else:
            parts.append({"text": "ℹ️ Описание отсутствует.", "entity": MessageEntityItalic})
        
        return parts, buttons