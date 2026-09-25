"""Verrouillage de l'interface web : identifiant + mot de passe (WEB_USERNAME / WEB_PASSWORD).

- politique du mot de passe vérifiée au démarrage : longueur minimale, minuscule, majuscule,
  chiffre et caractère spécial — un mot de passe faible empêche le serveur de démarrer ;
- session : cookie HttpOnly signé HMAC-SHA256 (identifiant, expiration), sans état serveur ;
- clients sans navigateur (TUI, scripts) : HTTP Basic, ou le jeton WEB_API_TOKEN en Bearer ;
- anti-force brute : après N échecs depuis une même adresse, connexion refusée pendant un délai.

Aucune dépendance : hmac, hashlib et secrets de la bibliothèque standard.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import string
import threading
import time
from dataclasses import dataclass, field

SESSION_COOKIE = "veille_session"
SPECIALS = "!@#%^&*()-_=+[]{};:,.?/~|<>"


class AuthConfigError(RuntimeError):
    pass


def password_problems(password: str, min_length: int = 8) -> list[str]:
    """Règles non respectées (liste vide : mot de passe conforme)."""
    problems = []
    if len(password) < min_length:
        problems.append(f"au moins {min_length} caractères")
    if not re.search(r"[a-z]", password):
        problems.append("une minuscule")
    if not re.search(r"[A-Z]", password):
        problems.append("une majuscule")
    if not re.search(r"\d", password):
        problems.append("un chiffre")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("un caractère spécial")
    if password.strip() != password:
        problems.append("pas d'espace en début ou fin")
    return problems


def generate_password(length: int = 20) -> str:
    """Mot de passe aléatoire conforme, sans caractère gênant dans un .env (guillemets, $, #, espace)."""
    alphabet = string.ascii_letters + string.digits + "-_.!@%+*~^:,"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if not password_problems(candidate, min_length=length):
            return candidate


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class Throttle:
    """Échecs de connexion par adresse : blocage temporaire au-delà de `max_failures`."""

    max_failures: int = 5
    lock_seconds: int = 300
    window_seconds: int = 900
    _failures: dict[str, list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def locked_for(self, client: str, now: float | None = None) -> int:
        """Secondes de blocage restantes pour ce client (0 : autorisé)."""
        now = now or time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(client, []) if now - t < self.window_seconds]
            self._failures[client] = recent
            if len(recent) < self.max_failures:
                return 0
            return max(0, int(recent[-1] + self.lock_seconds - now) + 1)

    def failure(self, client: str, now: float | None = None) -> None:
        with self._lock:
            self._failures.setdefault(client, []).append(now or time.monotonic())
            if len(self._failures) > 10_000:  # borne mémoire : on oublie les plus anciens
                self._failures.pop(next(iter(self._failures)))

    def success(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)


class Authenticator:
    def __init__(self, username: str, password: str, *, session_hours: int = 12, secret: str | None = None,
                 min_length: int = 8, throttle: Throttle | None = None) -> None:
        if not username.strip():
            raise AuthConfigError("WEB_USERNAME est vide.")
        if problems := password_problems(password, min_length):
            raise AuthConfigError("WEB_PASSWORD trop faible : il faut " + ", ".join(problems) + ".")
        self.username = username
        self._password = _digest(password)
        self._user = _digest(username)
        self.session_seconds = session_hours * 3600
        # Sans WEB_SESSION_SECRET, clé aléatoire : les sessions expirent au redémarrage du serveur.
        self._key = _digest(secret) if secret else secrets.token_bytes(32)
        self.throttle = throttle or Throttle()

    def check(self, username: str, password: str) -> bool:
        """Comparaison à temps constant (empreintes de même longueur), identifiant ET mot de passe."""
        user_ok = hmac.compare_digest(_digest(username), self._user)
        password_ok = hmac.compare_digest(_digest(password), self._password)
        return user_ok and password_ok

    def issue(self, now: float | None = None) -> str:
        expires = int((now or time.time()) + self.session_seconds)
        payload = f"{_b64(self.username.encode())}.{expires}"
        signature = hmac.new(self._key, payload.encode(), hashlib.sha256).digest()
        return f"{payload}.{_b64(signature)}"

    def verify(self, token: str, now: float | None = None) -> str | None:
        """Identifiant de la session si le jeton est authentique et non expiré, sinon None."""
        try:
            user, expires, signature = token.split(".")
            expected = hmac.new(self._key, f"{user}.{expires}".encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(_unb64(signature), expected) or int(expires) < (now or time.time()):
                return None
            return _unb64(user).decode()
        except (ValueError, UnicodeDecodeError):
            return None

    def basic(self, header: str) -> bool:
        """En-tête « Authorization: Basic … » valide (TUI, scripts)."""
        if not header.startswith("Basic "):
            return False
        try:
            username, _, password = base64.b64decode(header[6:].strip()).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return self.check(username, password)
