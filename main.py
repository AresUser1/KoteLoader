# main.py
import asyncio
import logging
import re
import time
import os
import uuid
import random
from configparser import ConfigParser
from telethon import TelegramClient, events
from telethon.sessions import StringSession, MemorySession
from telethon.errors import AccessTokenInvalidError, AccessTokenExpiredError, FloodWaitError

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
    exit()

START_TIME = time.time()

async def heartbeat():
    while True:
        await asyncio.sleep(60)

async def ensure_inline_mode_enabled(user_client, bot_username):
    try:
        print(f"🔄 Проверяем настройки inline-режима для @{bot_username}...")
        async with user_client.conversation('@BotFather', timeout=40, exclusive=False) as conv:
            await conv.send_message('/cancel')
            await asyncio.sleep(0.5)
            
            await conv.send_message('/setinline')
            resp = await conv.get_response()
            
            if "Choose a bot" in resp.text:
                await conv.send_message(f"@{bot_username}")
                resp = await conv.get_response()

            if "placeholder" in resp.text.lower():
                await conv.send_message("Search...")
                await conv.get_response()
                print(f"✅ Inline-режим для @{bot_username} успешно активирован/обновлен.")
            elif "Success" in resp.text:
                print(f"✅ Inline-режим для @{bot_username} уже активен.")
            else:
                print(f"ℹ️ Ответ BotFather: {resp.text.splitlines()[0]}")
                 
    except Exception as e:
        print(f"⚠️ Не удалось автоматически включить inline-режим: {e}")
        print("   (Если меню не работает, включите его вручную в @BotFather -> Bot Settings -> Inline Mode)")

async def auto_create_bot(user_client):
    print("\n🤖 Начинаем автоматическое создание бота через @BotFather...")
    async with user_client.conversation('@BotFather', timeout=60, exclusive=True) as conv:
        try:
            await conv.send_message('/cancel')
            await asyncio.sleep(0.5)
            
            await conv.send_message('/newbot')
            resp = await conv.get_response()
            
            if "try again in" in resp.text:
                print("❌ BotFather просит подождать (флуд-лимит).")
                return None
            
            if "can't add more than" in resp.text:
                print("❌ ОШИБКА: Достигнут лимит созданных ботов.")
                print("   Удалите старых через /deletebot в @BotFather.")
                return None
            
            await conv.send_message("KoteLoader Userbot")
            resp = await conv.get_response()
            
            if "choose a username" not in resp.text.lower():
                print(f"⚠️ Сбой диалога с BotFather: {resp.text}")
                return None

            bot_token = None
            
            for attempt in range(5):
                random_part = uuid.uuid4().hex[:6]
                username_to_try = f"Kote_{random_part}_bot"
                await conv.send_message(username_to_try)
                resp = await conv.get_response()
                
                if "Done!" in resp.text:
                    match = re.search(r'(\d+:[a-zA-Z0-9_-]{35})', resp.text)
                    if match:
                        bot_token = match.group(1)
                        print(f"✅ Бот успешно создан: @{username_to_try}")
                        break
                elif "taken" in resp.text:
                    print(f"   Юзернейм {username_to_try} занят, пробую другой...")
                    continue
                else:
                    print(f"❌ Ошибка BotFather: {resp.text}")
                    return None

            if not bot_token:
                print("❌ Не удалось создать бота за 5 попыток.")
                return None
            
            return bot_token

        except asyncio.TimeoutError:
            print("❌ BotFather не ответил вовремя.")
            return None

async def all_messages_handler(event):
    for watcher_func, kwargs in loader.WATCHERS_REGISTRY:
        try:
            is_incoming = kwargs.get("incoming", False)
            is_outgoing = kwargs.get("outgoing", False)
            if (is_incoming and event.incoming) or (is_outgoing and event.outgoing):
                await watcher_func(event)
        except FloodWaitError as e:
            logging.warning(f"⏳ FloodWait в модуле: ожидание {e.seconds} сек.")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logging.error(f"⚠️ Ошибка в модуле ({watcher_func.__name__}): {e}")
            continue

async def ensure_folder_added(client):
    """Проверяет и добавляет папку каналов KoteLoader, если её нет."""
    try:
        from telethon import functions
        slug = "-PNK0knddLQ3MzAy"
        
        # Проверяем инвайт-ссылку папки
        invite = await client(functions.chatlists.CheckChatlistInviteRequest(slug=slug))
        
        # Если папка уже добавлена и обновлений нет
        if isinstance(invite, functions.chatlists.ChatlistInviteAlready):
            return
            
        # Если это новый инвайт или есть новые пиры (каналы)
        if hasattr(invite, 'peers'):
            print(f"\n📂 Обнаружена папка с обновлениями модулей. Добавляю...")
            await client(functions.chatlists.JoinChatlistInviteRequest(
                slug=slug,
                peers=invite.peers
            ))
            print("✅ Папка успешно добавлена в ваш список чатов!")
            
    except Exception as e:
        # Если папка уже есть, Telegram может выкинуть ошибку, просто игнорируем
        if "CHATLIST_ALREADY_JOINED" not in str(e):
            pass

async def start_clients():
    config = ConfigParser()
    config_file = "config.ini"

    if os.path.exists(config_file):
        config.read(config_file, encoding='utf-8')
    
    # ── быстрые хелперы до инициализации клиента ────────────────────────────
    _C="\033[96m"; _Y="\033[93m"; _W="\033[97m"; _M="\033[95m"
    _G="\033[92m"; _DIM="\033[2m"; _RST="\033[0m"
    def _info(t):   print(f"  {_C}›{_RST} {t}")
    def _prompt(t): return input(f"  {_M}?{_RST} {t}: ").strip()
    def _banner(t):
        print(f"\n{_C}╭{'─'*44}╮{_RST}")
        print(f"{_C}│{_RST}  {_W}{t:<42}{_RST}{_C}│{_RST}")
        print(f"{_C}╰{'─'*44}╯{_RST}")

    if not config.has_section("telethon"):
        _banner("ПЕРВОНАЧАЛЬНАЯ НАСТРОЙКА  •  KoteLoader")
        _info(f"Получи данные на {_Y}my.telegram.org{_RST} → API development tools")
        print()
        api_id   = _prompt("api_id")
        api_hash = _prompt("api_hash")
        session_name = "my_account"
        
        config['telethon'] = {
            'api_id': api_id, 
            'api_hash': api_hash, 
            'session_name': session_name
        }
        with open(config_file, 'w', encoding='utf-8') as f:
            config.write(f)
    else:
        api_id = config.getint("telethon", "api_id")
        api_hash = config.get("telethon", "api_hash")
        session_name = config.get("telethon", "session_name")

    # --- ПРАВКА: Используем стандартные параметры юзербота ---
    system_version = "Linux"
    device_model = "KoteLoader"
    app_version = "2.0.0"

    _info(f"Сессия: {_Y}{session_name}{_RST}  {_DIM}•  подключаюсь...{_RST}")
    
    user_client = TelegramClient(
        session_name, 
        api_id, 
        api_hash
    )
    user_client.device_model = "KoteLoader" # Только для текста в бэкапах

    # ── КАСТОМНЫЕ ЦВЕТА ─────────────────────────────────────────────────────
    R  = "\033[91m"; G  = "\033[92m"; Y  = "\033[93m"; C  = "\033[96m"
    B  = "\033[94m"; M  = "\033[95m"; W  = "\033[97m"; DIM= "\033[2m"; RST= "\033[0m"

    def banner(text):
        line = "─" * 44
        print(f"\n{C}╭{line}╮{RST}")
        print(f"{C}│{RST}  {W}{text:<42}{RST}{C}│{RST}")
        print(f"{C}╰{line}╯{RST}")

    def info(text):   print(f"  {C}›{RST} {text}")
    def ok(text):     print(f"  {G}✔{RST} {text}")
    def err(text):    print(f"  {R}✘{RST} {text}")
    def warn(text):   print(f"  {Y}⚠{RST} {text}")
    def prompt(text): return input(f"  {M}?{RST} {text}: ").strip()
    # ────────────────────────────────────────────────────────────────────────

    await user_client.connect()

    if not await user_client.is_user_authorized():
        banner("АВТОРИЗАЦИЯ  •  KoteLoader")
        print(f"\n  {DIM}Выберите способ входа:{RST}")
        print(f"  {Y}1{RST} — По номеру телефона")
        print(f"  {Y}2{RST} — Через QR-код\n")

        while True:
            choice = prompt("Ваш выбор (1/2)")
            if choice in ("1", "2"):
                break
            warn("Введите 1 или 2")

        async def _enter_2fa():
            from telethon.errors import PasswordHashInvalidError
            while True:
                pwd = input(f"  {M}?{RST} Пароль 2FA: ").strip()
                try:
                    await user_client.sign_in(password=pwd)
                    ok("2FA пройдена успешно!")
                    return
                except PasswordHashInvalidError:
                    err("Неверный пароль — попробуй ещё раз")
                except Exception as ex:
                    err(f"Ошибка 2FA: {ex}")
                    raise

        if choice == "2":
            banner("ВХОД ЧЕРЕЗ QR-КОД")
            print(f"\n  {DIM}Инструкция:{RST}")
            info(f"{W}Telegram{RST} → {Y}Настройки{RST} → {Y}Устройства{RST} → {Y}Подключить устройство{RST}")
            info("Наведи камеру на QR-код ниже\n")

            qr_login = await user_client.qr_login()
            logged_in = False
            attempt = 0

            while not logged_in:
                attempt += 1
                print(f"  {DIM}[ QR #{attempt}  •  обновится через 30 сек ]{RST}\n")
                try:
                    import qrcode as _qrcode
                    _qr = _qrcode.QRCode(version=1, border=1)
                    _qr.add_data(qr_login.url)
                    _qr.make(fit=True)
                    _qr.print_ascii(invert=True)
                except ImportError:
                    info(f"Ссылка для входа: {C}{qr_login.url}{RST}")
                    warn("pip install qrcode  — для отображения QR здесь")

                print(f"\n  {DIM}⌛ Ожидаю сканирования...{RST}")
                try:
                    await qr_login.wait(timeout=30)
                    logged_in = True
                except asyncio.TimeoutError:
                    warn("Время вышло — генерирую новый QR...")
                    qr_login = await user_client.qr_login()
                except Exception as ex:
                    if "SessionPasswordNeeded" in type(ex).__name__:
                        print()
                        banner("ДВУХФАКТОРНАЯ АУТЕНТИФИКАЦИЯ")
                        info("На аккаунте включена 2FA")
                        await _enter_2fa()
                        logged_in = True
                    else:
                        err(f"Неожиданная ошибка: {ex}")
                        raise

        else:
            banner("ВХОД ПО НОМЕРУ ТЕЛЕФОНА")
            phone_number = prompt("Номер (например +79001234567)")
            print()

            async def _send_code(force_sms=False):
                try:
                    sent = await user_client.send_code_request(phone_number, force_sms=force_sms)
                    method = "СМС" if force_sms else sent.type.__class__.__name__
                    info(f"Код отправлен  {DIM}({method}){RST}")
                    return sent
                except Exception as e:
                    err(f"Не удалось отправить код: {e}")
                    exit(1)

            sent = await _send_code(force_sms=False)
            signed_in = False

            while not signed_in:
                print(f"\n  {DIM}Введи код — или выбери действие:{RST}")
                print(f"  {Y}r{RST} — Отправить код повторно (в приложение)")
                print(f"  {Y}s{RST} — Отправить через СМС")
                print(f"  {Y}q{RST} — Выйти\n")

                code = prompt("Код / r / s / q")

                if code.lower() == "q":
                    exit(0)
                elif code.lower() in ("r", "s"):
                    force = code.lower() == "s"
                    sent = await _send_code(force_sms=force)
                    continue
                elif not code.strip():
                    warn("Пустой ввод — попробуй ещё раз")
                    continue

                try:
                    await user_client.sign_in(phone_number, code, phone_code_hash=sent.phone_code_hash)
                    signed_in = True
                except Exception as ex:
                    ex_name = type(ex).__name__
                    if "SessionPasswordNeeded" in ex_name:
                        print()
                        banner("ДВУХФАКТОРНАЯ АУТЕНТИФИКАЦИЯ")
                        info("На аккаунте включена 2FA")
                        await _enter_2fa()
                        signed_in = True
                    elif "PhoneCodeInvalid" in ex_name:
                        err("Неверный код — попробуй ещё раз или нажми r/s для нового")
                    elif "PhoneCodeExpired" in ex_name:
                        err("Код устарел!")
                        warn("Нажми  r  чтобы запросить новый")
                    elif "FloodWait" in ex_name:
                        secs = getattr(ex, "seconds", "?")
                        err(f"Слишком много попыток — подожди {secs} сек")
                        exit(1)
                    else:
                        err(f"Ошибка входа: {ex}")
                        exit(1)

    me = await user_client.get_me()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or str(me.id)
    ok(f"Вошёл как  {W}{name}{RST}  {DIM}(id: {me.id}){RST}")

    await ensure_folder_added(user_client)

    bot_client = None
    
    while True:
        bot_token = config.get("telethon", "bot_token", fallback=None)
        
        if not bot_token:
            print("\n🤖 Для работы меню нужен Бот-помощник.")
            print("1. Ввести токен вручную")
            print("2. Создать автоматически")
            
            while True:
                choice = input("Ваш выбор (1/2): ").strip()
                if choice == "1":
                    bot_token = input("Введите токен бота: ").strip()
                    break
                elif choice == "2":
                    bot_token = await auto_create_bot(user_client) 
                    if bot_token:
                        break
                    else:
                        print("⚠️ Не удалось создать бота. Введите токен вручную.")
                else:
                    print("Введите 1 или 2.")
            
            if bot_token:
                config['telethon']['bot_token'] = bot_token
                with open(config_file, 'w', encoding='utf-8') as f:
                    config.write(f)

        if bot_token:
            print(f"🚀 Проверка запуска бота...")
            try:
                bot_client = TelegramClient(None, api_id, api_hash)
                await bot_client.start(bot_token=bot_token)
                print("✅ Бот успешно запущен!")
                break 
            except (AccessTokenInvalidError, AccessTokenExpiredError):
                print(f"❌ ОШИБКА: Токен бота невалиден или устарел!")
                print("🗑 Удаляю старый токен, давайте создадим нового.")
                config.remove_option('telethon', 'bot_token')
                with open(config_file, 'w', encoding='utf-8') as f:
                    config.write(f)
                bot_token = None 
            except Exception as e:
                print(f"⚠️ Ошибка при запуске бота: {e}")
                print("Попробуем настроить заново...")
                config.remove_option('telethon', 'bot_token')
                with open(config_file, 'w', encoding='utf-8') as f:
                    config.write(f)
                bot_token = None

    db.init_db()
    if db.get_setting("debug_mode") == "True":
        logging.getLogger().setLevel(logging.DEBUG)
    
    loader.PREFIX = db.get_setting("prefix", default=".")
    print(f"ℹ️ Префикс команд: {loader.PREFIX}")

    user_client.bot_client = bot_client
    if bot_client: 
        bot_client.user_client = user_client
        try:
            bot_info = await bot_client.get_me()
            
            await asyncio.sleep(1) 
            await ensure_inline_mode_enabled(user_client, bot_info.username)
            
            # Отправка start самому себе, чтобы бот появился в диалогах
            await user_client.send_message(bot_info.username, "/start")
        except Exception as e:
             print(f"⚠️ Небольшая ошибка при инициализации диалога с ботом: {e}")

    panel_pattern = re.compile(f"^{re.escape(loader.PREFIX)}(panel|settings)(?:\\s+(.*))?", re.IGNORECASE)
    user_client.add_event_handler(user_panel_helper, events.NewMessage(pattern=panel_pattern, outgoing=True))
    user_client.add_event_handler(all_messages_handler)

    if bot_client:
        bot_client.add_event_handler(inline_query_handler, events.InlineQuery)
        bot_client.add_event_handler(callback_query_handler, events.CallbackQuery)

    me = await user_client.get_me()
    if db.get_user_level(me.id) != "OWNER":
        db.add_user(me.id, "OWNER")
        print(f"👑 Права владельца выданы: {me.first_name} (ID: {me.id})")

    return user_client, bot_client

async def main():
    user_client, bot_client = await start_clients()
    if not user_client: return
        
    worker_task = asyncio.create_task(command_worker(user_client))
    
    print("👥 Запускаю твинков...")
    try:
        twins_count = await twin_manager.start_all_twins()
        print(f"✅ Запущено твинков: {twins_count}")
    except Exception as e:
        print(f"⚠️ Ошибка при запуске твинков: {e}")

    print("\n🟢 KoteLoader полностью запущен! Напишите help в чате.")
    
    try:
        # Добавляем heartbeat в список задач
        tasks = [worker_task, user_client.run_until_disconnected(), heartbeat()]
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
