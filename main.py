# main.py
import asyncio
import logging
import re
import time
import os
import uuid
from configparser import ConfigParser
from telethon import TelegramClient, events
from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji

# --- БАЗОВАЯ НАСТРОЙКА ЛОГГИРОВАНИЯ ---
LOG_FILE = "kote_loader.log"
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8'), logging.StreamHandler()])
logging.getLogger('telethon').setLevel(logging.WARNING)

# --- ИМПОРТЫ КОМПОНЕНТОВ БОТА ---
try:
    from handlers.bot_callbacks import inline_query_handler, callback_query_handler
    from handlers.user_commands import user_panel_helper, module_inline_handler
    from workers.command_worker import command_worker
    from utils import database as db
    from utils import loader
    from utils.message_builder import build_message
except ImportError as e:
    print(f"Критическая ошибка: не удалось импортировать необходимый компонент: {e}")
    print("Пожалуйста, убедитесь, что все файлы проекта на месте.")
    exit()

START_TIME = time.time()

async def ensure_inline_mode_enabled(user_client, bot_username):
    """
    Проверяет и принудительно включает inline-режим для бота через BotFather.
    """
    try:
        print(f"🔄 Проверяем inline-режим для @{bot_username}...")
        async with user_client.conversation('@BotFather', timeout=30) as conv:
            await conv.send_message('/setinline')
            await conv.get_response()

            await conv.send_message(f"@{bot_username}")
            resp = await conv.get_response()

            if "placeholder" not in resp.text.lower():
                 print(f"⚠️ Не удалось выбрать бота @{bot_username} в BotFather для настройки inline.")
                 await conv.cancel_all()
                 return

            await conv.send_message("Введите команду...")
            await conv.get_response()
            print(f"✅ Inline-режим для @{bot_username} проверен и активен.")
    except asyncio.TimeoutError:
        print(f"⚠️  Таймаут при общении с BotFather для настройки inline-режима.")
    except Exception as e:
        print(f"⚠️  Произошла ошибка при настройке inline-режима: {e}")

async def create_new_bot_with_botfather(api_id, api_hash, session_name):
    """
    Автоматически создает нового бота и включает для него inline-режим.
    """
    async with TelegramClient(session_name, api_id, api_hash) as client:
        print("\n🤖 Начинаем диалог с @BotFather для автоматического создания бота...")
        async with client.conversation('@BotFather', timeout=60) as conv:
            try:
                await conv.send_message('/newbot')
                resp = await conv.get_response()
                if "try again in" in resp.text:
                    return None

                if "How are we going to call it?" not in resp.text:
                    print(f"❌ Ошибка: BotFather не ответил ожидаемо. Ответ: {resp.text}")
                    return None

                await conv.send_message("KoteLoaderBot")
                print(f" > Отправлено имя: KoteLoaderBot")
                resp = await conv.get_response()
                if "choose a username" not in resp.text:
                    print(f"❌ Ошибка: BotFather не ответил ожидаемо. Ответ: {resp.text}")
                    return None

                bot_token = None
                bot_username = None
                for attempt in range(3):
                    random_part = uuid.uuid4().hex[:8]
                    username_to_try = f"KoteLoader_{random_part}_bot"
                    await conv.send_message(username_to_try)
                    print(f" > Отправлен юзернейм: {username_to_try} (попытка {attempt + 1})")
                    resp = await conv.get_response()
                    if "Sorry, this username is already taken" in resp.text:
                        print("   Этот юзернейм уже занят. Генерируем новый...")
                        continue
                    elif "Done! Congratulations" in resp.text:
                        match = re.search(r'(\d+:[a-zA-Z0-9_-]{35})', resp.text)
                        if match:
                            bot_token = match.group(1)
                            bot_username = username_to_try
                            print("✅ Бот успешно создан! Токен получен.")
                            break
                        else:
                            print("❌ Не удалось найти токен в сообщении BotFather.")
                            return None
                    else:
                        print(f"❌ Непредвиденная ошибка. Ответ: {resp.text}")
                        return None

                if not bot_token:
                    print("❌ Не удалось подобрать уникальный юзернейм за 3 попытки.")
                    return None

                print(" > Включаем inline-режим для нового бота...")
                await conv.send_message('/setinline')
                await conv.get_response()
                await conv.send_message(f"@{bot_username}")
                await conv.get_response()
                await conv.send_message("Введите команду...")
                await conv.get_response()
                print("✅ Inline-режим включен.")

                return bot_token

            except asyncio.TimeoutError:
                print("❌ Диалог с BotFather прерван по таймауту.")
                return None


async def all_messages_handler(event):
    """Передает сообщения всем зарегистрированным наблюдателям."""
    for watcher_func, kwargs in loader.WATCHERS_REGISTRY:
        is_incoming = kwargs.get("incoming", False)
        is_outgoing = kwargs.get("outgoing", False)
        if (is_incoming and event.incoming) or (is_outgoing and event.outgoing):
            await watcher_func(event)


async def start_clients():
    config = ConfigParser()
    config_file = "config.ini"

    if not os.path.exists(config_file):
        print(f"Файл конфигурации '{config_file}' не найден. Приступим к созданию...")
        print("Пожалуйста, введите данные вашего Telegram-аккаунта для входа.")
        api_id = input("Введите ваш api_id: ")
        api_hash = input("Введите ваш api_hash: ")

        session_name = ""
        while not session_name.strip():
            session_name = input("Введите имя сессии (например, my_account): ")
            if not session_name.strip():
                print("❌ Имя сессии не может быть пустым. Пожалуйста, введите его.")

        bot_token = await create_new_bot_with_botfather(api_id, api_hash, session_name)
        if not bot_token:
            print("\nНе удалось автоматически создать бота. Завершение работы.")
            return None, None
        config['telethon'] = {'api_id': api_id, 'api_hash': api_hash, 'session_name': session_name, 'bot_token': bot_token}
        with open(config_file, 'w', encoding='utf-8') as f:
            config.write(f)
        print(f"\n✅ Конфигурация успешно сохранена в '{config_file}'.")
        print("Пожалуйста, перезапустите бота командой: python3 main.py")
        return None, None

    config.read(config_file, encoding='utf-8')
    api_id = config.getint("telethon", "api_id")
    api_hash = config.get("telethon", "api_hash")

    session_name = config.get("telethon", "session_name", fallback=None)
    if not session_name:
        print(f"❌ Ошибка в '{config_file}': параметр 'session_name' пустой или отсутствует.")
        print("   Пожалуйста, исправьте файл или удалите его, чтобы пройти настройку заново.")
        return None, None

    bot_token = config.get("telethon", "bot_token", fallback=None)

    db.init_db()
    if db.get_setting("debug_mode") == "True":
        logging.getLogger().setLevel(logging.DEBUG)
        print("🐞 Включен режим отладки.")
    prefix = db.get_setting("prefix", default=".")
    loader.PREFIX = prefix
    print(f"Префикс команд из БД: {prefix}")

    user_client = TelegramClient(session_name, api_id, api_hash)
    bot_client = None

    print("🚀 Запускаем user-клиент...")
    await user_client.start()
    print("✅ User-клиент успешно запущен!")

    if bot_token:
        print("🚀 Запускаем bot-клиент...")
        bot_client = TelegramClient(None, api_id, api_hash)
        await bot_client.start(bot_token=bot_token)
        print("✅ Bot-клиент успешно запущен!")

        bot_me = await bot_client.get_me()
        await ensure_inline_mode_enabled(user_client, bot_me.username)
    else:
        print("\n⚠️  ВНИМАНИЕ: 'bot_token' не найден в config.ini. Бот-клиент не будет запущен. Функции бота недоступны.\n")

    user_client.bot_client = bot_client
    if bot_client:
        bot_client.user_client = user_client

    if bot_client:
        try:
            print(" Warming up entity cache...")
            bot_info = await bot_client.get_me()
            ping_msg = await user_client.send_message(bot_info.username, "/start")
            await ping_msg.delete()
            await user_client.get_dialogs(1)
            print(" Entity cache warmed up.")
        except Exception as e:
            print(f" Could not warm up entity cache: {e}")

    panel_pattern = re.compile(f"^{re.escape(prefix)}(panel|settings)(?:\\s+(.*))?", re.IGNORECASE)
    user_client.add_event_handler(user_panel_helper, events.NewMessage(pattern=panel_pattern, outgoing=True))
    
    # ----- БЛОК-ПЕРЕХВАТЧИК ДЛЯ module_inline_handler БЫЛ УДАЛЕН ОТСЮДА -----

    user_client.add_event_handler(all_messages_handler)

    if bot_client:
        bot_client.add_event_handler(inline_query_handler, events.InlineQuery)
        bot_client.add_event_handler(callback_query_handler, events.CallbackQuery)

    me = await user_client.get_me()
    if db.get_user_level(me.id) != "OWNER":
        db.add_user(me.id, "OWNER")
        print(f"Владелец определен и записан в БД: {me.first_name} (ID: {me.id})")

    return user_client, bot_client


async def main():
    user_client, bot_client = await start_clients()

    if not user_client:
        print("Не удалось запустить user-клиент. Выход.")
        return
        
    report_chat_id_str = db.get_setting("restart_report_chat_id")
    if report_chat_id_str:
        try:
            report_chat_id = int(report_chat_id_str)
            client = user_client
            client.modules = {}
            all_modules = loader.get_all_modules()
            for module_name in all_modules:
                # Убрали передачу chat_id, чтобы избежать отправки сообщений при старте
                await loader.load_module(client, module_name)
            
            loaded_modules_count = len(getattr(client, 'modules', {}))
            ROCKET_EMOJI_ID = 5445284980978621387
            SUCCESS_EMOJI_ID = 5255813619702049821
            report_parts = [
                {"text": "🚀", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ROCKET_EMOJI_ID}},
                {"text": " Перезагрузка успешно завершена!", "entity": MessageEntityBold},
                {"text": "\n\n"},
                {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_EMOJI_ID}},
                {"text": " Загружено модулей: ", "entity": MessageEntityBold},
                {"text": str(loaded_modules_count), "entity": MessageEntityCode},
            ]
            text, entities = build_message(report_parts)
            await user_client.send_message(report_chat_id, text, formatting_entities=entities)
        except Exception as e:
            print(f"Не удалось отправить отчёт о перезагрузке: {e}")
        finally:
            db.set_setting("restart_report_chat_id", "")

    try:
        tasks = [
            command_worker(user_client),
            user_client.run_until_disconnected()
        ]
        if bot_client:
            tasks.append(bot_client.run_until_disconnected())
        
        await asyncio.gather(*tasks)

    finally:
        print("\nЗавершение работы...")
        db.close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nБот остановлен вручную.")