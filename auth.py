"""Registration validation and password hashing for the demo app."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets


def validate_registration(name: str, email: str, phone: str, password: str) -> tuple[str, str, str]:
    name = " ".join(name.split())
    email = email.strip().lower()
    phone = phone.strip()
    if len(name) < 2 or len(name) > 100:
        raise ValueError("Enter a name between 2 and 100 characters.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Enter a valid email address.")
    if not re.fullmatch(r"\+?[0-9][0-9 ()-]{7,18}", phone):
        raise ValueError("Enter a valid phone number.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    if len(password) > 256:
        raise ValueError("Password must be 256 characters or fewer.")
    return name, email, phone


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_hex, expected_hex = encoded.split("$", maxsplit=2)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected_hex)
    except (ValueError, TypeError):
        return False