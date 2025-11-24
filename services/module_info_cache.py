# services/module_info_cache.py
import json
import importlib.util
from pathlib import Path
import re
import ast

BASE_DIR = Path(__file__).parent.parent
MODULES_DIR = BASE_DIR / "modules"
MODULES_INFO_FILE = BASE_DIR / "modules_info.json"

def extract_docstring(content: str) -> str:
    """
    Безопасно извлекает docstring из исходного кода Python,
    игнорируя импорты и сам код.
    """
    try:
        tree = ast.parse(content)
        return ast.get_docstring(tree) or ""
    except Exception:
        return ""

def parse_manifest(content: str) -> dict:
    """
    Парсит содержимое файла модуля.
    1. Извлекает Docstring.
    2. Находит внутри него блок <manifest> для метаданных.
    3. Удаляет блок <manifest> из Docstring, чтобы получить чистое описание.
    """
    # 1. Получаем весь текст docstring
    docstring = extract_docstring(content)
    
    meta = {
        "version": "N/A", 
        "source": None, 
        "author": "Неизвестно", 
        "description": "Описание отсутствует."
    }
    
    if not docstring:
        return meta

    # 2. Ищем манифест внутри docstring
    match = re.search(r"<manifest>(.*?)</manifest>", docstring, re.DOTALL)
    
    if match:
        manifest_block = match.group(0) # Весь блок <manifest>...</manifest>
        manifest_content = match.group(1) # То, что внутри
        
        # Парсим ключи внутри манифеста
        for line in manifest_content.split("\n"):
            line = line.strip()
            if ":" in line and not line.startswith("•"):
                key, value = line.split(":", 1)
                meta[key.strip().lower()] = value.strip()
        
        # 3. Описание — это docstring МИНУС манифест
        clean_description = docstring.replace(manifest_block, "").strip()
    else:
        # Если манифеста нет, весь docstring — это описание
        clean_description = docstring.strip()

    if clean_description:
        meta["description"] = clean_description
        
    return meta

def get_module_info(module_name: str) -> str:
    """Возвращает только описание модуля (для меню)."""
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
    """Кеширует описания всех модулей в JSON."""
    print("Кеширование информации о модулях...")
    info = {}
    
    for module_path in MODULES_DIR.rglob("*.py"):
        if module_path.name.startswith("_"):
            continue
        
        module_import_name = ".".join(module_path.relative_to(MODULES_DIR).with_suffix("").parts)
        
        try:
            # Читаем файл как текст, чтобы не исполнять код при кешировании
            content = module_path.read_text(encoding='utf-8')
            manifest = parse_manifest(content)
            info[module_import_name] = manifest["description"]
        except Exception as e:
            # print(f"Не удалось получить информацию из {module_import_name}: {e}")
            info[module_import_name] = "Описание отсутствует."
            
    with MODULES_INFO_FILE.open("w", encoding="utf-8") as f:
        json.dump(info, f, indent=4, ensure_ascii=False)
    print(f"ℹ️ Информация о {len(info)} модулях закеширована.")