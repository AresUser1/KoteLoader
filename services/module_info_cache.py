# services/module_info_cache.py
import json
import importlib.util
from pathlib import Path
import re

BASE_DIR = Path(__file__).parent.parent
MODULES_DIR = BASE_DIR / "modules"
MODULES_INFO_FILE = BASE_DIR / "modules_info.json"

def parse_manifest(content: str) -> dict:
    """Парсит текстовый манифест в формате ключ: значение."""
    match = re.search(r"<manifest>(.*?)</manifest>", content, re.DOTALL)
    if not match:
        return {"version": "N/A", "source": None, "author": "Неизвестно", "description": "Описание отсутствует."}
    
    manifest_text = match.group(1).strip()
    lines = manifest_text.split("\n")
    
    meta = {}
    description_start = 0
    
    for i, line in enumerate(lines):
        if ":" in line and not line.strip().startswith("•"):  # metadata
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = value.strip()
        elif line.strip() == "" and not meta:  # пустая строка после метаданных
            description_start = i + 1
            break
        else:  # началось описание
            description_start = i
            break
    
    description = "\n".join(lines[description_start:]).strip()
    
    return {
        "version": meta.get("version", "N/A"),
        "source": meta.get("source"),
        "author": meta.get("author", "Неизвестно"),
        "description": description or "Описание отсутствует."
    }

def parse_clean_description(doc: str) -> str:
    """
    Извлекает из docstring чистое описание, удаляя блок <manifest>.
    """
    if not doc:
        return "Описание отсутствует."

    description = re.sub(r"<manifest>.*?</manifest>", "", doc, flags=re.DOTALL).strip()
    return description or "Описание отсутствует."

def get_module_info(module_name: str) -> str:
    """Возвращает только описание модуля (без метаданных)."""
    module_path = MODULES_DIR / f"{module_name}.py"
    if not module_path.exists():
        return "Описание отсутствует."
    
    try:
        content = module_path.read_text(encoding='utf-8')
        manifest = parse_manifest(content)
        return manifest["description"]
    except Exception:
        return "Описание отсутствует."

def cache_modules_info():
    """Собирает __doc__ из всех модулей, очищает его от манифеста и сохраняет в modules_info.json."""
    print("Кеширование информации о модулях...")
    info = {}
    
    for module_path in MODULES_DIR.rglob("*.py"):
        if module_path.name.startswith("_"):
            continue
        
        module_import_name = ".".join(module_path.relative_to(MODULES_DIR).with_suffix("").parts)
        
        try:
            spec = importlib.util.spec_from_file_location(module_import_name, module_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                doc_string = getattr(module, "__doc__", "")
                clean_description = parse_clean_description(doc_string)
                info[module_import_name] = clean_description
            else:
                info[module_import_name] = "Не удалось загрузить спецификацию модуля."
        except Exception as e:
            print(f"Не удалось получить информацию из модуля {module_import_name}: {e}")
            info[module_import_name] = f"Ошибка чтения: {e}"
            
    with MODULES_INFO_FILE.open("w", encoding="utf-8") as f:
        json.dump(info, f, indent=4, ensure_ascii=False)
    print(f"ℹ️ Информация о {len(info)} модулях закеширована.")