# utils/security.py

import ast

# --- ПРАВИЛА БЕЗОПАСНОСТИ ---
BLOCK_LIST = {
    "functions": {"os.system", "exec", "eval", "__import__"},
    "strings": {".session", "config.ini"},
}
WARN_LIST = {
    "imports": {"subprocess", "shutil", "ftplib", "smtplib"},
    "functions": {"open", "getattr", "setattr"},
}
INFO_LIST = {
    "imports": {"requests", "aiohttp", "socket"},
}

class CodeVisitor(ast.NodeVisitor):
    """Обходит дерево кода и ищет опасные узлы."""
    def __init__(self):
        self.threats = set()
        self.level = "safe"

    def _update_level(self, new_level: str):
        """Обновляет уровень угрозы с учетом приоритета."""
        levels = {"safe": 0, "info": 1, "warning": 2, "block": 3}
        if levels[new_level] > levels[self.level]:
            self.level = new_level

    def visit_Call(self, node):
        """Анализирует вызовы функций."""
        func_name = ""
        if isinstance(node.func, ast.Attribute):
            if hasattr(node.func, 'value') and isinstance(node.func.value, ast.Name):
                func_name = f"{node.func.value.id}.{node.func.attr}"
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id

        if func_name in BLOCK_LIST["functions"]:
            self.threats.add(f"Обнаружен критический вызов функции: `{func_name}`")
            self._update_level("block")
        if func_name in WARN_LIST["functions"]:
            self.threats.add(f"Используется функция, требующая внимания: `{func_name}`")
            self._update_level("warning")
        self.generic_visit(node)

    def visit_Import(self, node):
        """Анализирует импорты."""
        for alias in node.names:
            if alias.name in BLOCK_LIST.get("imports", {}):
                self.threats.add(f"Импортируется критическая библиотека: `{alias.name}`")
                self._update_level("block")
            elif alias.name in WARN_LIST.get("imports", {}):
                self.threats.add(f"Импортируется потенциально опасная библиотека: `{alias.name}`")
                self._update_level("warning")
            elif alias.name in INFO_LIST.get("imports", {}):
                self.threats.add(f"Модуль будет выполнять веб-запросы через: `{alias.name}`")
                self._update_level("info")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        """Анализирует импорты."""
        if node.module in BLOCK_LIST.get("imports", {}):
            self.threats.add(f"Импорт из критической библиотеки: `{node.module}`")
            self._update_level("block")
        elif node.module in WARN_LIST.get("imports", {}):
            self.threats.add(f"Импорт из потенциально опасной библиотеки: `{node.module}`")
            self._update_level("warning")
        elif node.module in INFO_LIST.get("imports", {}):
            self.threats.add(f"Модуль будет выполнять веб-запросы через: `{node.module}`")
            self._update_level("info")
        self.generic_visit(node)

    def visit_Constant(self, node):
        """Анализирует строковые константы."""
        if isinstance(node.value, str):
            for blocked_str in BLOCK_LIST["strings"]:
                if blocked_str in node.value:
                    self.threats.add(f"В коде найдена опасная строка: `...{blocked_str}...`")
                    self._update_level("block")
        self.generic_visit(node)

def scan_code(code_content: str) -> dict:
    """Сканирует код и возвращает 'чистый' результат сканирования."""
    try:
        tree = ast.parse(code_content)
        visitor = CodeVisitor()
        visitor.visit(tree)
        
        if not visitor.threats:
            return {"level": "safe", "reasons": ["Опасных конструкций не найдено."]}
        
        return {"level": visitor.level, "reasons": sorted(list(visitor.threats))}
        
    except SyntaxError as e:
        return {"level": "block", "reasons": [f"Синтаксическая ошибка в коде: {e}"]}
    except Exception as e:
        return {"level": "block", "reasons": [f"Не удалось проанализировать код: {e}"]}

def check_permission(event, min_level: str = "TRUSTED") -> bool:
    """
    Проверяет права пользователя.
    min_level: "OWNER" или "TRUSTED"
    """
    from utils import database as db
    
    user_level = db.get_user_level(event.sender_id)
    
    if min_level == "OWNER" and user_level != "OWNER":
        return False
    if min_level == "TRUSTED" and user_level not in ["OWNER", "TRUSTED"]:
        return False
    
    return True