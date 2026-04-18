# compat/validators.py
"""
Эмуляция loader.validators из Heroku/Hikka.
Валидаторы здесь — чисто декларативные объекты.
Реальная валидация не нужна (мы просто хотим чтобы модули загружались),
но тип сохраняется — пригодится для будущего UI настроек.

Поддерживает Hikka-стиль вложенных валидаторов:
  Hidden(String())      ← позиционный аргумент-валидатор
  Hidden(inner=String()) ← keyword-аргумент
"""


class _Validator:
    """Базовый класс-заглушка для всех валидаторов."""
    def __init__(self, *args, **kwargs):
        # Первый позиционный аргумент может быть вложенным валидатором (Hikka-стиль)
        # Например: Hidden(String()), Series(String()), и т.д.
        # Просто сохраняем его, реальная валидация всё равно не нужна
        self._inner = args[0] if args and isinstance(args[0], _Validator) else None
        self._kwargs = kwargs

    def validate(self, value):
        # Если есть вложенный валидатор — делегируем ему
        if self._inner is not None:
            return self._inner.validate(value)
        return value  # Без строгой валидации — просто возвращаем как есть

    def __repr__(self):
        if self._inner is not None:
            return f"{self.__class__.__name__}({self._inner!r})"
        return f"{self.__class__.__name__}({self._kwargs})"


class Hidden(_Validator):
    """Скрытое поле (пароли, API-ключи). Оборачивает другой валидатор."""
    pass


class Boolean(_Validator):
    """True/False."""
    def validate(self, value):
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes")


class Integer(_Validator):
    """Целое число с опциональными min/max."""
    def __init__(self, *args, minimum=None, maximum=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, value):
        v = int(value)
        if self.minimum is not None:
            v = max(v, self.minimum)
        if self.maximum is not None:
            v = min(v, self.maximum)
        return v


class Float(_Validator):
    """Число с плавающей точкой с опциональными min/max."""
    def __init__(self, *args, minimum=None, maximum=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, value):
        v = float(value)
        if self.minimum is not None:
            v = max(v, self.minimum)
        if self.maximum is not None:
            v = min(v, self.maximum)
        return v


class String(_Validator):
    """Строка с опциональными ограничениями длины."""
    def __init__(self, *args, min_len=None, max_len=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.min_len = min_len
        self.max_len = max_len

    def validate(self, value):
        v = str(value)
        if self.min_len is not None and len(v) < self.min_len:
            raise ValueError(f"Минимальная длина: {self.min_len}")
        if self.max_len is not None and len(v) > self.max_len:
            v = v[:self.max_len]
        return v


class Choice(_Validator):
    """Одно из заданных значений."""
    def __init__(self, choices=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # choices может прийти как первый позиционный аргумент-список
        # или как вложенный валидатор — обрабатываем оба случая
        if isinstance(choices, (list, tuple)):
            self.choices = list(choices)
            self._inner = None
        elif isinstance(choices, _Validator):
            self.choices = []
            self._inner = choices
        else:
            self.choices = []

    def validate(self, value):
        if self.choices and value in self.choices:
            return value
        return self.choices[0] if self.choices else value


class MultiChoice(_Validator):
    """Несколько из заданных значений."""
    def __init__(self, choices=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if isinstance(choices, (list, tuple)):
            self.choices = list(choices)
            self._inner = None
        elif isinstance(choices, _Validator):
            self.choices = []
            self._inner = choices
        else:
            self.choices = []

    def validate(self, value):
        if isinstance(value, list):
            return [v for v in value if not self.choices or v in self.choices]
        return []


class Series(_Validator):
    """Список значений. Принимает строку с разделителем или список."""
    def __init__(self, *args, separator=",", **kwargs):
        super().__init__(*args, **kwargs)
        self.separator = separator

    def validate(self, value):
        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            items = [v.strip() for v in value.split(self.separator) if v.strip()]
        else:
            items = [str(value)]
        # Если есть вложенный валидатор — применяем к каждому элементу
        if self._inner is not None:
            items = [self._inner.validate(item) for item in items]
        return items


class URL(_Validator):
    """URL-строка. Проверяет что начинается с http:// или https://."""
    def validate(self, value):
        v = str(value).strip()
        if v and not v.startswith(("http://", "https://", "tg://")):
            raise ValueError(f"Некорректный URL: {v!r}")
        return v


class Regex(_Validator):
    """Строка, соответствующая регулярному выражению."""
    def __init__(self, pattern=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if isinstance(pattern, str):
            import re as _re
            self.pattern = pattern
            self._compiled = _re.compile(pattern)
            self._inner = None
        elif isinstance(pattern, _Validator):
            self.pattern = None
            self._compiled = None
            self._inner = pattern
        else:
            self.pattern = None
            self._compiled = None

    def validate(self, value):
        v = str(value)
        if self._compiled is not None and not self._compiled.search(v):
            raise ValueError(f"Значение не соответствует паттерну {self.pattern!r}")
        return v
