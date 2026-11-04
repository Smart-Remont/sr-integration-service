import re

# Stricter than the uuid type on purpose: this is the form MyNCA sends, and a value that passes
# here can never fail in client.sign_tab__modify after the signature row exists.
_UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def is_uuid_text(value: str) -> bool:
    return _UUID_TEXT.fullmatch(value) is not None
