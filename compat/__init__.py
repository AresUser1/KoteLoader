# compat/__init__.py
# Пакет совместимости с модулями Heroku/Hikka

# Устанавливаем shim сразу при импорте пакета — до загрузки любых модулей.
# Это гарантирует что "from aiogram.xxx import yyy" работает везде.
try:
    from compat.aiogram_shim import install_aiogram_shim
    install_aiogram_shim()
except Exception:
    pass