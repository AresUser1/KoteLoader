# workers/command_worker.py
import json
import asyncio
import traceback
import time
from pathlib import Path
from utils.loader import get_all_modules, load_module, unload_module, reload_module
from services.state_manager import update_state_file
from services.module_info_cache import cache_modules_info
from utils import database as db
from utils.message_builder import build_message
from telethon.tl.types import MessageEntityBold, MessageEntityCode

COMMAND_FILE = Path(__file__).parent.parent / "command.json"

async def command_worker(user_client):
    """Постоянно проверяет command.json и выполняет команды."""
    print("👤 Воркер юзербота запущен.")
    user_client.modules = {}
    
    cache_modules_info()
    
    # --- ЗАГРУЗКА МОДУЛЕЙ ---
    all_modules = get_all_modules()
    print(f"Найдено модулей: {len(all_modules)}")
    
    for module in all_modules:
        try:
            # print(f"Загружаю {module}...") # Можно раскомментировать для отладки
            await load_module(user_client, module)
        except Exception:
            print(f"Ошибка загрузки модуля {module}:\n{traceback.format_exc()}")
            
    update_state_file(user_client)
    
    # --- ОТПРАВКА ОТЧЕТА О ПЕРЕЗАГРУЗКЕ (Теперь здесь!) ---
    report_chat_id_str = db.get_setting("restart_report_chat_id")
    if report_chat_id_str:
        try:
            report_chat_id = int(report_chat_id_str)
            
            restart_start_time_str = db.get_setting("restart_start_time")
            restart_duration_text = ""
            if restart_start_time_str:
                try:
                    restart_start_time = float(restart_start_time_str)
                    duration = time.time() - restart_start_time
                    restart_duration_text = f"{duration:.2f} сек"
                except Exception:
                    pass 
            
            loaded_modules_count = len(getattr(user_client, 'modules', {}))
            
            report_parts = [
                {"text": "🚀 Перезагрузка успешно завершена!", "entity": MessageEntityBold},
                {"text": "\n\n"},
                {"text": "✅ Загружено модулей: ", "entity": MessageEntityBold},
                {"text": str(loaded_modules_count), "entity": MessageEntityCode},
            ]
            
            if restart_duration_text:
                report_parts.extend([
                    {"text": "\n"},
                    {"text": "⏱️ Время перезапуска: ", "entity": MessageEntityBold},
                    {"text": restart_duration_text, "entity": MessageEntityCode},
                ])
            
            text, entities = build_message(report_parts)
            await user_client.send_message(report_chat_id, text, formatting_entities=entities)
        except Exception as e:
            print(f"Не удалось отправить отчёт о перезагрузке: {e}")
        finally:
            # Очищаем флаги, чтобы не спамить при следующем запуске (если он не через .restart)
            db.set_setting("restart_report_chat_id", "")
            db.set_setting("restart_start_time", "")
    # ------------------------------------------------------

    while True:
        if COMMAND_FILE.exists():
            try:
                with COMMAND_FILE.open("r") as f:
                    data = json.load(f)
                
                command, module_name, chat_id = data.get("command"), data.get("module_name"), data.get("chat_id")
                if not all([command, module_name, chat_id]):
                    raise ValueError("Некорректные данные в command.json")

                modules_to_process = get_all_modules() if module_name == "all" else [module_name]
                
                report_lines = []
                for mod in modules_to_process:
                    try:
                        if command == "load":
                            result_text = await load_module(user_client, mod)
                        elif command == "unload":
                            result_text = await unload_module(user_client, mod)
                        elif command == "reload":
                            result_text = await reload_module(user_client, mod)
                        report_lines.append(result_text.get('message', str(result_text)))
                    except Exception as e:
                        report_lines.append(f"<b>Ошибка с модулем {mod}:</b> <code>{e}</code>")
                
                await user_client.send_message(
                    chat_id, 
                    f"<b>Отчет о выполнении команды '{command} {module_name}':</b>\n\n" + "\n".join(report_lines), 
                    parse_mode="html"
                )
                
                update_state_file(user_client)

            except Exception as e:
                print(f"🔥 Ошибка в воркере: {e}")
                traceback.print_exc()
            finally:
                if COMMAND_FILE.exists(): 
                    COMMAND_FILE.unlink()
        
        await asyncio.sleep(1)