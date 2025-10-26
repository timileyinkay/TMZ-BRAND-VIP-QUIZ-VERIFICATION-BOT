# --- imghdr.py (replacement for Python 3.13) ---
# Simple re-implementation to make python-telegram-bot work

def what(file, h=None):
    if h is None:
        if isinstance(file, (str, bytes)):
            try:
                with open(file, 'rb') as f:
                    h = f.read(32)
            except Exception:
                return None
        else:
            return None

    if h[:2] == b'\xff\xd8':
        return 'jpeg'
    if h[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if h[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if h[:2] == b'BM':
        return 'bmp'
    if h[:4] == b'\x00\x00\x01\x00':
        return 'ico'
    if h[:4] == b'RIFF' and h[8:12] == b'WEBP':
        return 'webp'
    return None
