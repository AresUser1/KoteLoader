# compat/imghdr_shim.py
"""
Shim для модуля `imghdr`, удалённого из Python 3.13.

imghdr.what(file) — определяет тип изображения по magic bytes.
Возвращает строку: 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tiff', 'rgb', 'pbm',
'pgm', 'ppm', 'xbm', None.

Устанавливается в sys.modules['imghdr'] если модуль недоступен.
"""

import sys
import io


def what(file, h=None):
    """
    Определяет тип изображения.

    file — путь к файлу (str) или file-like объект (поддерживает read/seek).
    h    — байты для проверки (если уже прочитаны, file игнорируется).

    Возвращает строку типа ('jpeg', 'png', ...) или None.
    """
    if h is None:
        if isinstance(file, (str, bytes)):
            try:
                with open(file, "rb") as f:
                    h = f.read(32)
            except Exception:
                return None
        else:
            # file-like object
            try:
                pos = file.tell()
            except Exception:
                pos = None
            try:
                h = file.read(32)
            except Exception:
                return None
            finally:
                if pos is not None:
                    try:
                        file.seek(pos)
                    except Exception:
                        pass

    if len(h) < 4:
        return None

    # JPEG: FF D8 FF
    if h[:3] == b'\xff\xd8\xff':
        return 'jpeg'

    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if h[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'

    # GIF: GIF87a или GIF89a
    if h[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'

    # WebP: RIFF????WEBP
    if h[:4] == b'RIFF' and h[8:12] == b'WEBP':
        return 'webp'

    # BMP: BM
    if h[:2] == b'BM':
        return 'bmp'

    # TIFF: little-endian (II) или big-endian (MM)
    if h[:4] in (b'II\x2a\x00', b'MM\x00\x2a'):
        return 'tiff'

    # SGI RGB: \x01\xda
    if h[:2] == b'\x01\xda':
        return 'rgb'

    # PBM/PGM/PPM: P1-P6
    if h[:2] in (b'P1', b'P2', b'P3', b'P4', b'P5', b'P6'):
        fmt = {b'P1': 'pbm', b'P4': 'pbm',
               b'P2': 'pgm', b'P5': 'pgm',
               b'P3': 'ppm', b'P6': 'ppm'}
        return fmt.get(h[:2])

    # XBM: #define
    if h[:7] == b'#define':
        return 'xbm'

    # ICO: 00 00 01 00
    if h[:4] == b'\x00\x00\x01\x00':
        return 'ico'

    # AVIF / HEIF: ....ftypavif / ....ftypheic
    if h[4:8] == b'ftyp':
        brand = h[8:12]
        if brand in (b'avif', b'avis'):
            return 'avif'
        if brand in (b'heic', b'heix', b'hevc', b'hevx'):
            return 'heic'

    return None


def install_imghdr_shim():
    """
    Устанавливает shim в sys.modules['imghdr'] если imghdr недоступен.
    Безопасно вызывать повторно.
    """
    # Пробуем нативный imghdr
    try:
        import imghdr as _native
        # Если импортировался — всё ок, shim не нужен
        if hasattr(_native, 'what') and not getattr(_native, '_kote_shim', False):
            return
    except ImportError:
        pass

    # Устанавливаем shim
    mod = sys.modules.get('imghdr')
    if mod is not None and getattr(mod, '_kote_shim', False):
        return  # уже установлен

    shim_mod = sys.modules.get(__name__.rsplit('.', 1)[0] + '.imghdr_shim') or \
               sys.modules.get('compat.imghdr_shim') or \
               sys.modules.get(__name__)

    # Создаём новый модуль-обёртку
    import types as _types
    fake = _types.ModuleType('imghdr')
    fake.__doc__ = "imghdr shim (Python 3.13+ compatibility)"
    fake._kote_shim = True
    fake.what = what
    fake.tests = []  # для совместимости с кодом который трогает imghdr.tests

    sys.modules['imghdr'] = fake
