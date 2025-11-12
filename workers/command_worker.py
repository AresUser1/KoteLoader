import json
import asyncio
import traceback
from pathlib import Path
from utils.loader import get_all_modules, load_module, unload_module, reload_module
from services.state_manager import update_state_file
from services.module_info_cache import cache_modules_info

COMMAND_FILE = Path(__file__).parent.parent / "command.json"

async def command_worker(user_client):
    """Постоянно проверяет command.json и выполняет команды."""
    print("👤 Воркер юзербота запущен.")
    user_client.modules = {}
    
    cache_modules_info()
    
    for module in get_all_modules():
        try:
            await load_module(user_client, module)
        except Exception:
            print(f"Ошибка загрузки модуля {module}:\n{traceback.format_exc()}")
            
    update_state_file(user_client)

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
                        report_lines.append(result_text)
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