"""Generate a bcrypt hash for ADMIN_PASSWORD_HASH.

uv run python -m app.hashpw
"""

from __future__ import annotations

import getpass

from .auth import hash_password


def main() -> None:
    password = getpass.getpass("password: ")
    if not password:
        raise SystemExit("empty password")
    print(hash_password(password))


if __name__ == "__main__":
    main()
