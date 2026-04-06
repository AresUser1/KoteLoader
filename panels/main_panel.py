# panels/main_panel.py

import re
from telethon.tl.custom import Button
from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityItalic
from utils.loader import get_all_modules
from services.state_manager import get_loaded_modules

def build_main_panel(page: int = 0, search_query: str = None, as_text: bool = False, user_client=None):
    """
    Собирает главное меню со списком модулей.
    - Файловые модули: статус ✅/❌ из state.json (учитывает heroku: префикс)
    - Heroku/Hikka модули: всегда ✅, отдельная последняя страница
    - Дубликатов нет: если модуль есть и как файл и как heroku — один раз как файл
    """
    raw_loaded = get_loaded_modules()  # {'about', 'admin', 'heroku:FHeta', ...}

    def _get_module_name_from_file(filepath):
        """Читает strings['name'] из файла модуля если есть."""
        try:
            import ast as _ast2
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as _f:
                _src = _f.read(4000)  # читаем только первые 4KB
            _m = re.search(r'["\']name["\'\s]*:\s*["\']([^"\']+)["\']', _src)
            if _m:
                return _m.group(1)
        except Exception:
            pass
        return None

    def _norm(s):
        """Нормализует имя модуля для сравнения.
        'GoyPulse V9' -> 'goypulse'
        'TagAll 2.0'  -> 'tagall'
        'tagall2.0'   -> 'tagall'
        'PointSentenceCase' -> 'pointsentencecase'
        'point'       -> 'point'
        """
        s = s.lower()
        # Убираем версию в конце: ' v9', ' 2.0', '2.0'
        s = re.sub(r'[\s._-]+v?[\d][\d.]*$', '', s)
        # Убираем все не-буквенные символы
        s = re.sub(r'[^a-z]', '', s)
        return s

    # Нормализуем: убираем heroku: префикс и нормализуем имя
    loaded_names = set()
    for m in raw_loaded:
        if m.startswith("heroku:"):
            loaded_names.add(_norm(m[len("heroku:"):]))
        else:
            loaded_names.add(_norm(m))

    all_modules = sorted(get_all_modules(user_client))  # исключаем heroku-модули с file_name

    # Строим маппинг: норм_имя_файла -> список норм_имён модуля (из strings['name'])
    from pathlib import Path as _PanelPath
    _modules_dir = _PanelPath(__file__).parent.parent / "modules"
    _file_to_modnames = {}  # norm(filename) -> set of norm(possible_names)
    for _mfile in all_modules:
        _fp = _modules_dir / f"{_mfile}.py"
        _nfile = _norm(_mfile)
        _file_to_modnames[_nfile] = {_nfile}
        if _fp.exists():
            _real_name = _get_module_name_from_file(_fp)
            if _real_name:
                _file_to_modnames[_nfile].add(_norm(_real_name))

    # Все нормализованные имена файлов (включая реальные имена модулей)
    _all_norm_file_names = set()
    for _names in _file_to_modnames.values():
        _all_norm_file_names.update(_names)

    # Heroku-модули которых НЕТ в файлах (истинно внешние)
    heroku_only = []
    if user_client is not None:
        for key in getattr(user_client, "modules", {}):
            if key.startswith("heroku:"):
                hname = key[len("heroku:"):]
                if _norm(hname) not in _all_norm_file_names:
                    heroku_only.append(hname)

    if search_query:
        sq = search_query.lower()
        all_modules = [m for m in all_modules if sq in m.lower()]
        heroku_only = [m for m in heroku_only if sq in m.lower()]

    per_page = 8
    total_file_pages = max(1, (len(all_modules) + per_page - 1) // per_page)
    total_pages = total_file_pages + (1 if heroku_only else 0)
    page = max(0, min(page, total_pages - 1))

    is_heroku_page = bool(heroku_only) and page >= total_file_pages

    buttons = []

    if is_heroku_page:
        h_row = []
        for i, hmod in enumerate(heroku_only):
            h_row.append(Button.inline(f"✅ {hmod}", data=f"module:{hmod}"))
            if (i + 1) % 2 == 0:
                buttons.append(h_row)
                h_row = []
        if h_row:
            buttons.append(h_row)
    else:
        start = page * per_page
        end = start + per_page
        row = []
        for i, module in enumerate(all_modules[start:end]):
            # Проверяем все возможные имена модуля (filename + strings['name'])
            _mod_norms = _file_to_modnames.get(_norm(module), {_norm(module)})
            status_emoji = "✅" if _mod_norms & loaded_names else "❌"
            row.append(Button.inline(f"{status_emoji} {module}", data=f"module:{module}"))
            if (i + 1) % 2 == 0:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(Button.inline("⬅️ Назад", data=f"page:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(Button.inline("Вперёд ➡️", data=f"page:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([Button.inline("🌐 Глобальные действия", data="global_menu")])
    buttons.append([
        Button.inline("🔄 Обновить", data="refresh"),
        Button.inline("✖️ Закрыть", data="close_panel"),
    ])

    # total_all = уникальные модули (файлы + внешние heroku без дублей)
    total_all = len(all_modules) + len(heroku_only)
    # total_loaded = сколько из них реально загружено
    # loaded_names уже нормализован (без heroku: префикса, lower)
    # Считаем: файловые загруженные + heroku_only загруженные (они всегда в памяти)
    file_loaded = sum(1 for m in all_modules
                       if _file_to_modnames.get(_norm(m), {_norm(m)}) & loaded_names)
    total_loaded = file_loaded + len(heroku_only)
    page_label = f"{page + 1}/{total_pages}" if total_pages > 1 else ""

    if as_text:
        text = "<b>Панель управления KoteLoader</b>\n\n"
        if search_query:
            text += f"🔍 Результаты поиска: <b>{search_query}</b>\n\n"
        text += f"✅ Загружено: {total_loaded} из {total_all} модулей.\n"
        if heroku_only:
            text += f"🔌 Heroku/Hikka: {len(heroku_only)} доп. модулей.\n"
        if page_label:
            text += f"📄 Страница: {page_label}\n"
        if is_heroku_page:
            text += "\n<i>Heroku/Hikka модули (внешние):</i>\n"
        return text, buttons
    else:
        parts = []
        parts.append({"text": "Панель управления KoteLoader", "entity": MessageEntityBold})
        parts.append({"text": "\n\n"})
        if search_query:
            parts.append({"text": "🔍 Результаты поиска: "})
            parts.append({"text": search_query, "entity": MessageEntityBold})
            parts.append({"text": "\n\n"})
        parts.append({"text": f"✅ Загружено: {total_loaded} из {total_all} модулей.\n"})
        if heroku_only:
            parts.append({"text": f"🔌 Heroku/Hikka: {len(heroku_only)} доп. модулей.\n"})
        if page_label:
            parts.append({"text": f"📄 Страница: {page_label}\n"})
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