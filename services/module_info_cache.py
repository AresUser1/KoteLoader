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
    Поддерживает оба формата:
    - KoteLoader: <manifest>...</manifest> внутри docstring
    - Heroku/Hikka: # meta developer: @xxx  и  __version__ = (x,y,z)
    """
    meta = {
        "version": "N/A",
        "source": None,
        "author": "Неизвестно",
        "description": "Описание отсутствует."
    }

    # --- Версия: пробуем все известные форматы ---

    # 1. # version: x.y.z  (комментарий в шапке, напр. MellstroyVoice)
    _ver_comment = re.search(r'^\s*#\s*[Vv]ersion[:\s]+(.+)', content, re.MULTILINE)
    if _ver_comment:
        meta["version"] = _ver_comment.group(1).strip()

    # 2. __version__ = (x, y, z)  или  __version__ = "x.y.z"  (FHeta, chatgpt)
    if meta["version"] == "N/A":
        _ver_dunder = re.search(r'^__version__\s*=\s*(.+)', content, re.MULTILINE)
        if _ver_dunder:
            raw = _ver_dunder.group(1).strip()
            # кортеж (9, 3, 7) → "9.3.7"
            tup = re.match(r'\((.+?)\)', raw)
            if tup:
                meta["version"] = ".".join(p.strip() for p in tup.group(1).split(","))
            else:
                # строка "1.0.0"
                s = re.match(r'["\'](.+?)["\']', raw)
                if s:
                    meta["version"] = s.group(1)

    # 3. self._module_version = "x.y.z"  (goypulse)
    if meta["version"] == "N/A":
        _ver_attr = re.search(r'_module_version\s*=\s*["\'](.+?)["\']', content)
        if _ver_attr:
            meta["version"] = _ver_attr.group(1)

    # 1. Пробуем стандартный docstring модуля
    docstring = extract_docstring(content)

    # 2. Если нет модульного docstring — ищем docstring класса (Heroku-стиль)
    if not docstring:
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    class_doc = ast.get_docstring(node)
                    if class_doc:
                        docstring = class_doc
                        break
        except Exception:
            pass

    # 3. Ищем Heroku/Hikka meta-теги в комментариях: # meta key: value
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# meta developer:"):
            meta["author"] = line.split(":", 1)[1].strip()
        elif line.startswith("# meta source:"):
            meta["source"] = line.split(":", 1)[1].strip()
        elif line.startswith("# meta desc:"):
            meta["description"] = line.split(":", 1)[1].strip()
        elif line.startswith("# meta link:"):
            link_val = line.split(":", 1)[1].strip()
            if link_val.startswith(".dlm "):
                link_val = link_val[5:].strip()
            meta["source"] = link_val

    # 3b. Ищем _cls_doc — приоритет: strings_ru > strings
    if meta["description"] == "Описание отсутствует.":
        import ast as _ast
        try:
            tree = _ast.parse(content)
            cls_doc_val = None
            cls_doc_ru_val = None
            for node in _ast.walk(tree):
                if isinstance(node, _ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, _ast.Assign):
                            for target in item.targets:
                                tname = getattr(target, 'id', None)
                                if tname in ("strings", "strings_ru") and isinstance(item.value, _ast.Dict):
                                    for k, v in zip(item.value.keys, item.value.values):
                                        kval = getattr(k, 's', None) or (k.value if isinstance(k, _ast.Constant) else None)
                                        if kval == "_cls_doc" and isinstance(v, (_ast.Str, _ast.Constant)):
                                            vval = getattr(v, 's', None) or getattr(v, 'value', None)
                                            if vval:
                                                if tname == "strings_ru":
                                                    cls_doc_ru_val = str(vval).strip()
                                                else:
                                                    cls_doc_val = str(vval).strip()
            result = cls_doc_ru_val or cls_doc_val
            if result:
                meta["description"] = result
        except Exception:
            pass

    if not docstring:
        return meta

    # 5. Ищем KoteLoader <manifest> блок внутри docstring
    match = re.search(r"<manifest>(.*?)</manifest>", docstring, re.DOTALL)

    if match:
        manifest_block = match.group(0)
        manifest_content = match.group(1)

        for line in manifest_content.split("\n"):
            line = line.strip()
            if ":" in line and not line.startswith("•"):
                key, value = line.split(":", 1)
                meta[key.strip().lower()] = value.strip()

        clean_description = docstring.replace(manifest_block, "").strip()
    else:
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