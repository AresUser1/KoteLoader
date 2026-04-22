# main.py
import asyncio
import logging
import re
import time
import os
import sys
import uuid
import random
from configparser import ConfigParser
from telethon import TelegramClient, events, connection
from telethon.sessions import StringSession, MemorySession
from telethon.errors import AccessTokenInvalidError, AccessTokenExpiredError, FloodWaitError

LOG_FILE = "kote_loader.log"
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8'), logging.StreamHandler()])
logging.getLogger('telethon').setLevel(logging.WARNING)

# ── Защита от двойного запуска (database is locked) ─────────────────────────
_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".koteloader.lock")

def _is_pid_alive(pid: int) -> bool:
    """Проверяет жив ли процесс с данным PID через /proc (надёжно на Android/Termux)."""
    # Проверяем через /proc — работает везде на Linux/Android
    try:
        return os.path.exists(f"/proc/{pid}")
    except Exception:
        pass
    # Fallback: сигнал 0
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False

def _acquire_lock():
    # Если lock-файл существует — проверяем жив ли тот процесс
    if os.path.exists(_LOCK_FILE):
        try:
            with open(_LOCK_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid() and _is_pid_alive(old_pid):
                print(f"❌ KoteLoader уже запущен (PID {old_pid})! Закройте предыдущий процесс.")
                sys.exit(1)
        except (ValueError, OSError):
            pass
        # Процесс мёртв или файл повреждён — перезаписываем
        try:
            os.unlink(_LOCK_FILE)
        except OSError:
            pass
    # Записываем свой PID
    try:
        with open(_LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass  # Не критично если не удалось записать

def _release_lock():
    try:
        if os.path.exists(_LOCK_FILE):
            with open(_LOCK_FILE, "r") as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.unlink(_LOCK_FILE)
    except Exception:
        pass

import atexit as _atexit
_acquire_lock()
_atexit.register(_release_lock)
# ────────────────────────────────────────────────────────────────────────────

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

# ══════════════════════════════════════════════════════════════════════════════
# MTProto прокси — вспомогательные функции
# ══════════════════════════════════════════════════════════════════════════════

def _parse_mtproto_link(link: str):
    """
    Разбирает ссылку вида:
      tg://proxy?server=HOST&port=PORT&secret=SECRET
    или просто:
      HOST PORT SECRET   (через пробел/запятую)
    Возвращает {'server': ..., 'port': ..., 'secret': ...} или None.
    """
    import urllib.parse
    link = link.strip()
    if not link:
        return None

    # Формат tg://proxy?... или https://t.me/proxy?...
    if "proxy?" in link or "server=" in link:
        try:
            if link.startswith("tg://"):
                qs = link.split("?", 1)[1]
            else:
                qs = link.split("?", 1)[1]
            params = dict(urllib.parse.parse_qsl(qs))
            server = params.get("server", "").strip()
            port   = params.get("port", "").strip()
            secret = params.get("secret", "").strip()
            if server and port.isdigit() and secret:
                return {"server": server, "port": int(port), "secret": secret}
        except Exception:
            pass
        return None

    # Формат "host port secret"
    parts = link.replace(",", " ").split()
    if len(parts) == 3 and parts[1].isdigit():
        return {"server": parts[0], "port": int(parts[1]), "secret": parts[2]}

    return None


def _serialize_proxies(proxies: list) -> str:
    """Сериализует список прокси в строку для config.ini."""
    parts = []
    for p in proxies:
        parts.append(f"{p['server']}|{p['port']}|{p['secret']}")
    return ";".join(parts)


def _deserialize_proxies(raw: str) -> list:
    """Десериализует строку из config.ini в список прокси."""
    result = []
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        parts = item.split("|")
        if len(parts) == 3 and parts[1].isdigit():
            result.append({"server": parts[0], "port": int(parts[1]), "secret": parts[2]})
    return result


def _build_proxy_kwargs(proxies: list) -> dict:
    """
    Возвращает kwargs для TelegramClient.
    Telethon сам обрабатывает секрет через normalize_secret —
    нужно передавать hex-строку как есть (с ee/dd префиксом).
    """
    if not proxies:
        return {}
    px = proxies[0]
    return {
        "connection": connection.ConnectionTcpMTProxyRandomizedIntermediate,
        "proxy": (px["server"], px["port"], px["secret"]),
    }


def _setup_mtproto_proxies(prompt_fn, info_fn, C, Y, G, RST) -> list:
    """
    Интерактивный диалог для ввода одного или нескольких MTProto прокси.
    Возвращает список {'server', 'port', 'secret'}.
    """
    proxies = []
    print()
    while True:
        raw = prompt_fn(f"Ссылка на прокси или Enter чтобы {'пропустить' if not proxies else 'закончить'}")
        if not raw:
            break
        parsed = _parse_mtproto_link(raw)
        if parsed:
            proxies.append(parsed)
            print(f"  {G}✔{RST} Прокси добавлен: {Y}{parsed['server']}:{parsed['port']}{RST}")
            print(f"  {C}›{RST} Можно добавить ещё один или нажми Enter чтобы продолжить\n")
        else:
            print(f"  \033[91m✘\033[0m Не удалось разобрать ссылку. Попробуй ещё раз.")
    return proxies

# ══════════════════════════════════════════════════════════════════════════════


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
    """
    Каждый запуск:
    - Если папки нет — создаёт и добавляет все каналы.
    - Если папка есть — подтягивает новые/удалённые каналы.
    - Если не удалось — отправляет ссылку в Избранное.
    """
    from telethon import functions
    from telethon.tl.types.chatlists import ChatlistInvite, ChatlistInviteAlready

    C="\033[96m"; G="\033[92m"; Y="\033[93m"; DIM="\033[2m"; RST="\033[0m"
    SLUG = "K668gR0TrwRlNmJi"
    FOLDER_LINK = "https://t.me/addlist/K668gR0TrwRlNmJi"

    async def _send_to_saved(reason: str):
        """Отправляет ссылку на папку в Избранное при ошибке."""
        try:
            await client.send_message(
                "me",
                f"⚠️ KoteLoader не смог автоматически добавить папку с каналами.\n"
                f"Причина: {reason}\n\n"
                f"👉 Добавь папку вручную:\n{FOLDER_LINK}"
            )
            print(f"  {Y}⚠{RST}  Ссылка на папку отправлена в Избранное.")
        except Exception as ex:
            print(f"  {DIM}[folder] Не удалось отправить в Избранное: {ex}{RST}")

    try:
        invite = await client(functions.chatlists.CheckChatlistInviteRequest(slug=SLUG))

        if isinstance(invite, ChatlistInviteAlready):
            # Папка уже есть — проверяем появились ли новые/удалённые каналы
            if invite.missing_peers:
                print(f"\n  {Y}⚠{RST}  В папке KoteLoader появились новые каналы ({len(invite.missing_peers)} шт.) — добавляю...")
                await client(functions.chatlists.JoinChatlistUpdatesRequest(
                    filter_id=invite.filter_id,
                    peers=invite.missing_peers
                ))
                print(f"  {G}✔{RST}  Новые каналы добавлены в папку!")
            return

        if isinstance(invite, ChatlistInvite):
            # Папки нет — добавляем
            folder_name = invite.title.text if hasattr(invite.title, "text") else str(invite.title)
            count = len(invite.peers)
            print(f"\n  {C}›{RST}  Добавляю папку {Y}{folder_name}{RST} ({count} каналов)...")
            await client(functions.chatlists.JoinChatlistInviteRequest(
                slug=SLUG,
                peers=invite.peers
            ))
            print(f"  {G}✔{RST}  Папка успешно добавлена в список чатов!")

    except Exception as e:
        err = str(e)
        if any(x in err for x in ("CHATLIST_ALREADY_JOINED", "FILTER_ID_INVALID")):
            return  # Норма — папка уже есть
        print(f"  {DIM}[folder] {err}{RST}")
        await _send_to_saved(err)

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
        while True:
            api_id = _prompt("api_id")
            if api_id.strip().isdigit():
                break
            print(f"  \033[91m✘\033[0m api_id должен быть числом, попробуй ещё раз")
        while True:
            api_hash = _prompt("api_hash")
            if api_hash.strip():
                break
            print(f"  \033[91m✘\033[0m api_hash не может быть пустым")
        session_name = "my_account"

        # ── MTProto прокси — спрашиваем при первой установке ────────────────
        _banner("НАСТРОЙКА MTProto ПРОКСИ  •  KoteLoader")
        _info("Если Telegram заблокирован — настрой MTProto прокси.")
        _info(f"Формат ссылки: {_Y}tg://proxy?server=...&port=...&secret=...{_RST}")
        print(f"  {_DIM}(нажми Enter чтобы пропустить){_RST}\n")
        proxies_list = _setup_mtproto_proxies(_prompt, _info, _C, _Y, _G, _RST)

        config['telethon'] = {
            'api_id': api_id,
            'api_hash': api_hash,
            'session_name': session_name
        }
        if proxies_list:
            config['mtproto'] = {'proxies': _serialize_proxies(proxies_list)}
        with open(config_file, 'w', encoding='utf-8') as f:
            config.write(f)
        # ─────────────────────────────────────────────────────────────────────
    else:
        _raw_api_id = config.get("telethon", "api_id", fallback="").strip()
        if not _raw_api_id or not _raw_api_id.isdigit():
            print(f"\n  \033[91m✘\033[0m  api_id в config.ini пустой или не является числом.")
            print(f"  Открой config.ini и впиши корректный api_id (только цифры).\n")
            sys.exit(1)
        api_id = int(_raw_api_id)
        api_hash = config.get("telethon", "api_hash", fallback="").strip()
        if not api_hash:
            print(f"\n  \033[91m✘\033[0m  api_hash в config.ini пустой.\n")
            sys.exit(1)
        session_name = config.get("telethon", "session_name", fallback="my_account")

    # ── Загружаем MTProto прокси из конфига ─────────────────────────────────
    proxies_list = []
    if config.has_section("mtproto"):
        _raw = config.get("mtproto", "proxies", fallback="").strip()
        if _raw:
            proxies_list = _deserialize_proxies(_raw)

    # ── Подключение с прокси (или без) ──────────────────────────────────────
    proxy_kwargs = _build_proxy_kwargs(proxies_list)

    _info(f"Сессия: {_Y}{session_name}{_RST}  {_DIM}•  подключаюсь...{_RST}")
    if proxy_kwargs:
        _info(f"Используется MTProto прокси: {_Y}{proxy_kwargs.get('proxy', ('?',))[0]}{_RST}")

    # УБРАН преждевременный TelegramClient здесь — он открывал my_account.session
    # до того как _make_client создавал второй клиент с тем же файлом,
    # что приводило к "database is locked". Клиент создаётся ниже через _make_client.

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

    # ── Вспомогательная функция создания клиента ────────────────────────────
    def _make_client(px_list):
        if px_list:
            pk = _build_proxy_kwargs(px_list)
        else:
            pk = {}
        c = TelegramClient(session_name, api_id, api_hash, **pk)
        c.device_model = "KoteLoader"
        return c

    # ── Подключение (с прокси или без, с фоллбэком) ─────────────────────────
    async def _try_connect_proxy(px):
        """Проверяет прокси через MemorySession (без записи на диск). Возвращает True или False."""
        _info(f"Попытка подключения через прокси {_Y}{px['server']}:{px['port']}{_RST} ...")
        c = None
        try:
            pk = _build_proxy_kwargs([px])
            c = TelegramClient(MemorySession(), api_id, api_hash, **pk)
            await asyncio.wait_for(c.connect(), timeout=15)
            if c.is_connected():
                print(f"  {_G}✔{_RST} Прокси {_Y}{px['server']}{_RST} работает!")
                return True
        except Exception as e:
            print(f"  \033[91m✘\033[0m {px['server']}: {e}")
        finally:
            if c is not None:
                try:
                    await c.disconnect()
                except Exception:
                    pass
        return False

    if proxies_list:
        connected_ok = False
        for px in proxies_list:
            connected_ok = await _try_connect_proxy(px)
            if connected_ok:
                proxy_kwargs = _build_proxy_kwargs([px])
                user_client = _make_client([px])
                await user_client.connect()
                break
        if not connected_ok:
            while True:
                print(f"\n  \033[91m✘\033[0m Ни один прокси не ответил.")
                print(f"  {_Y}1{_RST} — Ввести другие прокси")
                print(f"  {_Y}2{_RST} — Подключиться без прокси")
                ch = _prompt("Ваш выбор (1/2)")
                if ch == "1":
                    new_px = _setup_mtproto_proxies(_prompt, _info, _C, _Y, _G, _RST)
                    if not new_px:
                        continue
                    proxies_list.clear()
                    proxies_list.extend(new_px)
                    config['mtproto'] = {'proxies': _serialize_proxies(proxies_list)}
                    with open(config_file, 'w', encoding='utf-8') as f:
                        config.write(f)
                    for px in proxies_list:
                        connected_ok = await _try_connect_proxy(px)
                        if connected_ok:
                            proxy_kwargs = _build_proxy_kwargs([px])
                            user_client = _make_client([px])
                            await user_client.connect()
                            break
                    if connected_ok:
                        break
                elif ch == "2":
                    _info("Подключение без прокси...")
                    proxy_kwargs = {}
                    user_client = _make_client([])
                    await user_client.connect()
                    break
                else:
                    print("  Введи 1 или 2")
    else:
        user_client = _make_client([])
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
                # Фикс суррогатных символов при вводе на некоторых терминалах
                try:
                    pwd = pwd.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')
                    pwd = pwd.encode('utf-8').decode('utf-8')
                except Exception:
                    pass
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
                print(f"  {Y}q{RST} — Выйти\n")

                code = prompt("Код / r / q")

                if code.lower() == "q":
                    exit(0)
                elif code.lower() == "r":
                    sent = await _send_code(force_sms=False)
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

    # Сохраняем tg_id прямо на клиенте — Hikka-модули обращаются к client._tg_id
    user_client._tg_id = me.id
    user_client.tg_id = me.id

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
                bot_client = TelegramClient(None, api_id, api_hash, **proxy_kwargs)
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
            # Сохраняем username бота на user_client чтобы _InlineManager мог его использовать
            user_client.bot_username = bot_info.username or ""
            
            await asyncio.sleep(1) 
            await ensure_inline_mode_enabled(user_client, bot_info.username)
            
            # Отправка start самому себе, чтобы бот появился в диалогах
            await user_client.send_message(bot_info.username, "/start")
        except Exception as e:
             print(f"⚠️ Небольшая ошибка при инициализации диалога с ботом: {e}")

    panel_pattern = re.compile(f"^{re.escape(loader.PREFIX)}(panel|settings)(?:\\s+(.*))?", re.IGNORECASE)
    user_client.add_event_handler(user_panel_helper, events.NewMessage(pattern=panel_pattern, outgoing=True))

    # ── MTProto прокси команда (.proxy) ──────────────────────────────────────
    from handlers.proxy_handler import proxy_command_handler
    _proxy_pattern = re.compile(r"^\.proxy(?:\s+(.+))?$", re.IGNORECASE)
    user_client.add_event_handler(
        lambda e: proxy_command_handler(e, config, config_file, proxies_list),
        events.NewMessage(pattern=_proxy_pattern, outgoing=True)
    )
    # ─────────────────────────────────────────────────────────────────────────

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
        # Регистрируем watcher Silent Tags на каждом твин-клиенте
        try:
            from modules.stags import twin_silent_tags_watcher
            from telethon import events as _tl_events
            for _twin_name, _twin_client in twin_manager.clients.items():
                _twin_client._twin_name = _twin_name
                _twin_client.add_event_handler(
                    twin_silent_tags_watcher,
                    _tl_events.NewMessage(incoming=True)
                )
            print(f"[stags] Twin watchers зарегистрированы для {len(twin_manager.clients)} твинков.")
        except Exception as _twe:
            print(f"[stags] Не удалось зарегистрировать twin watchers: {_twe}")
        try:
            from modules.auto_manager import twin_auto_read_watcher
            from telethon import events as _tl_events
            for _twin_name, _twin_client in twin_manager.clients.items():
                _twin_client.add_event_handler(
                    twin_auto_read_watcher,
                    _tl_events.NewMessage(incoming=True)
                )
            print(f"[auto_manager] Twin read watchers зарегистрированы для {len(twin_manager.clients)} твинков.")
        except Exception as _awe:
            print(f"[auto_manager] Не удалось зарегистрировать twin read watchers: {_awe}")
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