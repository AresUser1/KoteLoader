# handlers/bot_callbacks.py

import traceback
import asyncio
import sys
import importlib
import re
from pathlib import Path

from telethon import events
from telethon.errors.rpcerrorlist import MessageNotModifiedError
from telethon.tl.custom import Button
from telethon.tl.types import InputBotInlineResult

from utils import database as db
from utils.loader import (
    INLINE_HANDLERS_REGISTRY, CALLBACK_REGISTRY,
    load_module, unload_module, reload_module, check_module_dependencies
)
# Импортируем все наши панели для построения меню
from panels.main_panel import build_main_panel
from panels.module_menu import build_module_menu
from panels.global_menu import build_global_menu
from panels.updates_panel import build_updates_panel
from services.state_manager import update_state_file

# --- ИЗМЕНЕНИЕ 1: ДОБАВЛЯЕМ НУЖНЫЙ ИМПОРТ ---
from modules.updater import check_for_updates


# --- ИЗМЕНЕНИЕ 2: СТАРАЯ ФУНКЦИЯ-ПОСРЕДНИК БОЛЬШЕ НЕ НУЖНА И УДАЛЕНА ---

async def updates_callback_handler(event):
    """
    Обрабатывает нажатия на кнопки обновления. Отправляет команды юзерботу.
    """
    action = event.pattern_match.group(1)

    await event.answer("Отправляю команду на обновление...")

    # Получаем юзер-клиент из бота
    user_client = event.client.user_client

    if action == "all":
        # Локальный импорт, чтобы избежать циклических зависимостей
        from modules.updater import check_for_updates
        
        updates = await check_for_updates()
        modules_to_update = [u["module_name"] for u in updates]

        for module_name in modules_to_update:
             # Отправляем команду от имени юзера, а не бота
             await user_client.send_message("me", f".update {module_name}")
             await asyncio.sleep(0.3) # Небольшая задержка, чтобы не создавать нагрузку

        await event.edit("✅ **Команды на обновление всех модулей отправлены!**", buttons=None, parse_mode='html')

    else: # Если обновляем один модуль
        module_name = action
        # Отправляем команду от имени юзера, а не бота
        await user_client.send_message("me", f".update {module_name}")
        await event.edit(f"✅ **Команда на обновление <code>{module_name}</code> отправлена!**", buttons=None, parse_mode='html')

# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ---

async def inline_query_handler(event: events.InlineQuery):
    """
    Динамически обрабатывает инлайн-запросы, находя подходящий обработчик.
    """
    if db.get_user_level(event.sender_id) not in ["OWNER", "TRUSTED"]:
        return

    query_text = event.text.strip()

    try:
        # --- ИЗМЕНЕНИЕ 3: ПОЛНОСТЬЮ ПЕРЕПИСАНА ЛОГИКА ОБРАБОТКИ ОБНОВЛЕНИЙ ---
        if query_text == "updates:check":
            # Вызываем функцию проверки обновлений прямо здесь
            updates_list = await check_for_updates()
            
            # Получаем текст и кнопки из панели
            text, buttons = build_updates_panel(updates_list)
            
            # Создаем ответ для инлайн-меню
            result = event.builder.article(
                title="Центр обновлений",
                description=f"Найдено обновлений: {len(updates_list)}",
                text=text,
                buttons=buttons,
                parse_mode="html"
            )
            await event.answer([result])
            return

        if query_text.startswith("module:"):
            module_name = query_text.split(":", 1)[1]
            check = check_module_dependencies(module_name)

            if check["status"] == "error":
                missing_lib = check["library"]
                text = (f"⚠️ **Ошибка в модуле `{module_name}`**\n\n"
                        f"Причина: отсутствует библиотека: `{missing_lib}`.")
                buttons = [[Button.inline(f"📦 Установить {missing_lib}", data=f"dep:install:{module_name}:{missing_lib}")],
                           [Button.inline("🗑️ Удалить модуль", data=f"dep:delete:{module_name}")]]
                result = event.builder.article(
                    title=f"Ошибка в модуле: {module_name}",
                    description=f"Отсутствует библиотека {missing_lib}",
                    text=text, buttons=buttons, parse_mode="md"
                )
            else:
                text, buttons = build_module_menu(module_name, as_text=True)
                result = event.builder.article(
                    title=f"Управление модулем: {module_name}",
                    description="Загрузка, выгрузка и перезагрузка.",
                    text=text, buttons=buttons, parse_mode="html"
                )

            await event.answer([result])
            return

        for pattern, handler_info in INLINE_HANDLERS_REGISTRY.items():
            match = pattern.match(query_text)
            if match:
                event.pattern_match = match
                text, buttons = await handler_info["func"](event)
                result = event.builder.article(
                    title=handler_info["title"],
                    description=handler_info["description"],
                    text=text, buttons=buttons, parse_mode="html"
                )
                await event.answer([result])
                return

        # Если ни один обработчик не подошел, показываем главную панель
        text, buttons = build_main_panel(search_query=query_text, as_text=True)
        result = event.builder.article(
            title="⚙️ Панель управления",
            description="Главное меню.",
            text=text, buttons=buttons, parse_mode="html"
        )
        await event.answer([result])
    except Exception:
        traceback.print_exc()

async def callback_query_handler(event: events.CallbackQuery):
    """
    Динамически обрабатывает нажатия на инлайн-кнопки.
    """
    if db.get_user_level(event.sender_id) not in ["OWNER", "TRUSTED"]:
        return await event.answer("🚫 Доступ запрещён.", alert=True)

    data = event.data.decode()
    user_client = event.client.user_client

    try:
        # --- ОБРАБОТКА НАЖАТИЙ КНОПОК ОБНОВЛЕНИЯ ---
        if data.startswith("do_update:"):
            event.pattern_match = re.match(r"do_update:(.+)", data)
            if event.pattern_match:
                await updates_callback_handler(event)
                return

        if data.startswith("dep:"):
            await event.answer()
            parts = data.split(":")
            action, module_name = parts[1], parts[2]

            if action == "install":
                library_name = parts[3]
                await event.edit(f"⏳ Начинаю установку `{library_name}`...")
                process = await asyncio.create_subprocess_shell(
                    f"{sys.executable} -m pip install {library_name}",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate()

                if process.returncode == 0:
                    importlib.invalidate_caches()
                    await event.edit(f"✅ Библиотека `{library_name}` установлена!\nПроверяю модуль `{module_name}` снова...")
                    check = check_module_dependencies(module_name)
                    if check["status"] == "ok":
                        text, buttons = build_module_menu(module_name, as_text=True)
                        await event.edit(text, buttons=buttons, parse_mode="html")
                    else:
                        new_missing_lib = check["library"]
                        text = (f"⚠️ **Ошибка в модуле `{module_name}`**\n\n"
                                f"Найдена еще одна отсутствующая библиотека: `{new_missing_lib}`.")
                        buttons = [[Button.inline(f"📦 Установить {new_missing_lib}", data=f"dep:install:{module_name}:{new_missing_lib}")],
                                   [Button.inline("🔙 Назад", data="back_to_main")]]
                        await event.edit(text, buttons=buttons, parse_mode="md")
                else:
                    output = stderr.decode().strip() or stdout.decode().strip()
                    await event.edit(f"❌ **Ошибка установки `{library_name}`:**\n"
                                     f"<code>{output}</code>", parse_mode="html")
                return

            elif action == "delete":
                MODULES_DIR = Path(__file__).parent.parent / "modules"
                module_path = MODULES_DIR / f"{module_name}.py"
                if module_path.exists():
                    module_path.unlink()
                    db.clear_module(module_name)
                    await event.answer(f"🗑️ Модуль {module_name} полностью удален.", alert=True)
                    text, buttons = build_main_panel(as_text=True)
                    await event.edit(text, buttons=buttons, parse_mode="html")
                else:
                    await event.answer(f"ℹ️ Модуль {module_name} уже был удален.", alert=True)
                return

        for pattern, handler_func in CALLBACK_REGISTRY.items():
            match = pattern.match(data)
            if match:
                event.pattern_match = match
                await handler_func(event)
                return

        text, buttons = None, None

        if data.startswith("load:"):
            module_name = data.split(":", 1)[1]
            if module_name == "all":
                from utils.loader import get_all_modules
                for mod in get_all_modules(): await load_module(user_client, mod)
                update_state_file(user_client)
                await event.answer("✅ Все модули загружены!", alert=True)
                text, buttons = build_main_panel(page=0, as_text=True)
            else:
                result = await load_module(user_client, module_name)
                update_state_file(user_client)
                await event.answer(result.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""), alert=True)
                text, buttons = build_module_menu(module_name, as_text=True)

        elif data.startswith("unload:"):
            module_name = data.split(":", 1)[1]
            if module_name == "all":
                for mod in list(user_client.modules.keys()): await unload_module(user_client, mod)
                update_state_file(user_client)
                await event.answer("🗑️ Все модули выгружены!", alert=True)
                text, buttons = build_main_panel(page=0, as_text=True)
            else:
                result = await unload_module(user_client, module_name)
                update_state_file(user_client)
                await event.answer(result.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""), alert=True)
                text, buttons = build_module_menu(module_name, as_text=True)

        elif data.startswith("reload:"):
            module_name = data.split(":", 1)[1]
            if module_name == "all":
                for mod in list(user_client.modules.keys()): await reload_module(user_client, mod)
                update_state_file(user_client)
                await event.answer("♻️ Все модули перезагружены!", alert=True)
                text, buttons = build_main_panel(page=0, as_text=True)
            else:
                result = await reload_module(user_client, module_name)
                update_state_file(user_client)
                await event.answer(result.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""), alert=True)
                text, buttons = build_module_menu(module_name, as_text=True)

        elif data.startswith("page:"):
            page = int(data.split(":")[1])
            text, buttons = build_main_panel(page=page, as_text=True)

        elif data.startswith("module:"):
            module_name = data.split(":")[1]
            text, buttons = build_module_menu(module_name, as_text=True)

        elif data == "global_menu":
            text, buttons = build_global_menu(as_text=True)

        elif data in ["back_to_main", "refresh"]:
            text, buttons = build_main_panel(page=0, as_text=True)

        if text and buttons:
            await event.edit(text, buttons=buttons, parse_mode="html")

    except MessageNotModifiedError:
        await event.answer() # Просто подтверждаем нажатие, если сообщение не изменилось
    except Exception:
        traceback.print_exc()
        await event.answer("Произошла ошибка при обработке вашего запроса.", alert=True)