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
    """Строка."""
    pass


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
    """Список строк. Оборачивает валидатор элементов."""
    pass


class URL(_Validator):
    """URL-строка."""
    pass


class Regex(_Validator):
    """Строка, соответствующая регулярному выражению."""
    def __init__(self, pattern=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if isinstance(pattern, str):
            self.pattern = pattern
            self._inner = None
        elif isinstance(pattern, _Validator):
            self.pattern = None
            self._inner = pattern
        else:
            self.pattern = None
