#main.py
import asyncio
import logging
import re
import time
import os
import uuid
from configparser import ConfigParser
from telethon import TelegramClient, events

LOG_FILE = "kote_loader.log"
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8'), logging.StreamHandler()])
logging.getLogger('telethon').setLevel(logging.WARNING)

try:
    from handlers.bot_callbacks import inline_query_handler, callback_query_handler
    from handlers.user_commands import user_panel_helper
    from workers.command_worker import command_worker
    from utils import database as db
    from utils import loader
    from services.twin_manager import twin_manager 
except ImportError as e:
    print(f"Критическая ошибка: не удалось импортировать необходимый компонент: {e}")
    print("Пожалуйста, убедитесь, что все файлы проекта на месте.")
    exit()

START_TIME = time.time()

async def ensure_inline_mode_enabled(user_client, bot_username):
    try:
        print(f"🔄 Проверяем inline-режим для @{bot_username}...")
        async with user_client.conversation('@BotFather', timeout=30) as conv:
            await conv.send_message('/setinline')
            await conv.get_response()
            await conv.send_message(f"@{bot_username}")
            resp = await conv.get_response()
            if "placeholder" not in resp.text.lower():
                 print(f"⚠️ Не удалось выбрать бота @{bot_username} в BotFather.")
                 await conv.cancel_all()
                 return
            await conv.send_message("Введите команду...")
            await conv.get_response()
            print(f"✅ Inline-режим для @{bot_username} проверен и активен.")
    except Exception as e:
        print(f"⚠️ Произошла ошибка при настройке inline-режима: {e}")

async def create_new_bot_with_botfather(api_id, api_hash, session_name):
    async with TelegramClient(session_name, api_id, api_hash) as client:
        print("\n🤖 Начинаем диалог с @BotFather...")
        async with client.conversation('@BotFather', timeout=60) as conv:
            try:
                await conv.send_message('/newbot')
                resp = await conv.get_response()
                if "try again in" in resp.text: return None
                if "How are we going to call it?" not in resp.text: return None
                await conv.send_message("KoteLoaderBot")
                resp = await conv.get_response()
                if "choose a username" not in resp.text: return None

                bot_token = None
                bot_username = None
                
                for attempt in range(3):
                    random_part = uuid.uuid4().hex[:8]
                    username_to_try = f"KoteLoader_{random_part}_bot"
                    await conv.send_message(username_to_try)
                    resp = await conv.get_response()
                    if "Done! Congratulations" in resp.text:
                        match = re.search(r'(\d+:[a-zA-Z0-9_-]{35})', resp.text)
                        if match:
                            bot_token = match.group(1)
                            bot_username = username_to_try
                            print("✅ Бот успешно создан!")
                            break
                    elif "taken" in resp.text: continue
                    else: return None

                if not bot_token: return None

                await conv.send_message('/setinline')
                await conv.get_response()
                await conv.send_message(f"@{bot_username}")
                await conv.get_response()
                await conv.send_message("Введите команду...")
                await conv.get_response()
                return bot_token

            except asyncio.TimeoutError:
                return None

async def all_messages_handler(event):
    for watcher_func, kwargs in loader.WATCHERS_REGISTRY:
        is_incoming = kwargs.get("incoming", False)
        is_outgoing = kwargs.get("outgoing", False)
        if (is_incoming and event.incoming) or (is_outgoing and event.outgoing):
            await watcher_func(event)

async def start_clients():
    config = ConfigParser()
    config_file = "config.ini"

    if not os.path.exists(config_file):
        print("Файл config.ini не найден. Запустите скрипт настройки.")
        return None, None

    config.read(config_file, encoding='utf-8')
    api_id = config.getint("telethon", "api_id")
    api_hash = config.get("telethon", "api_hash")
    session_name = config.get("telethon", "session_name", fallback=None)
    bot_token = config.get("telethon", "bot_token", fallback=None)

    if not session_name: return None, None

    db.init_db()
    if db.get_setting("debug_mode") == "True":
        logging.getLogger().setLevel(logging.DEBUG)
    
    loader.PREFIX = db.get_setting("prefix", default=".")
    print(f"Префикс: {loader.PREFIX}")

    user_client = TelegramClient(session_name, api_id, api_hash)
    bot_client = None

    print("🚀 Запускаем user-клиент...")
    await user_client.start()

    if bot_token:
        print("🚀 Запускаем bot-клиент...")
        bot_client = TelegramClient(None, api_id, api_hash)
        await bot_client.start(bot_token=bot_token)
        bot_me = await bot_client.get_me()
        await ensure_inline_mode_enabled(user_client, bot_me.username)
    else:
        print("⚠️ Bot-клиент не запущен (нет токена).")

    user_client.bot_client = bot_client
    if bot_client: bot_client.user_client = user_client

    if bot_client:
        try:
            bot_info = await bot_client.get_me()
            ping_msg = await user_client.send_message(bot_info.username, "/start")
            await ping_msg.delete()
            await user_client.get_dialogs(1)
        except: pass

    panel_pattern = re.compile(f"^{re.escape(loader.PREFIX)}(panel|settings)(?:\\s+(.*))?", re.IGNORECASE)
    user_client.add_event_handler(user_panel_helper, events.NewMessage(pattern=panel_pattern, outgoing=True))
    user_client.add_event_handler(all_messages_handler)

    if bot_client:
        bot_client.add_event_handler(inline_query_handler, events.InlineQuery)
        bot_client.add_event_handler(callback_query_handler, events.CallbackQuery)

    me = await user_client.get_me()
    if db.get_user_level(me.id) != "OWNER":
        db.add_user(me.id, "OWNER")

    return user_client, bot_client

async def main():
    user_client, bot_client = await start_clients()
    if not user_client: return
        
    worker_task = asyncio.create_task(command_worker(user_client))
    
    print("👥 Запускаю твинков...")
    twins_count = await twin_manager.start_all_twins()
    print(f"✅ Запущено твинков: {twins_count}")

    try:
        tasks = [worker_task, user_client.run_until_disconnected()]
        if bot_client: tasks.append(bot_client.run_until_disconnected())
        await asyncio.gather(*tasks)
    finally:
        print("\nЗавершение работы...")
        db.close_db()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nБот остановлен вручную.")