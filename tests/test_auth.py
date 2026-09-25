"""Verrouillage web : politique de mot de passe, sessions signées, anti-force brute."""

import base64

import pytest

from app.web.auth import AuthConfigError, Authenticator, Throttle, generate_password, password_problems


@pytest.mark.parametrize("password, missing", [
    ("Ab1!", "au moins 8 caractères"),
    ("ABCDEFG1!", "une minuscule"),
    ("abcdefg1!", "une majuscule"),
    ("Abcdefgh!", "un chiffre"),
    ("Abcdefg12", "un caractère spécial"),
])
def test_password_policy_lists_each_missing_rule(password, missing):
    assert missing in password_problems(password)


def test_generated_passwords_are_compliant_and_dotenv_safe():
    for _ in range(20):
        password = generate_password()
        assert not password_problems(password)
        assert not set(password) & set("'\"$#` \\")


def test_weak_password_prevents_startup():
    with pytest.raises(AuthConfigError, match="trop faible"):
        Authenticator("veille", "password")


def test_session_token_is_signed_and_expires():
    auth = Authenticator("veille", "Str0ng!Pass", session_hours=1, secret="k")
    token = auth.issue(now=1000)
    assert auth.verify(token, now=1001) == "veille"
    assert auth.verify(token, now=1000 + 3601) is None  # expiré
    user, expires, signature = token.split(".")
    assert auth.verify(f"{user}.{int(expires) + 999}.{signature}", now=1001) is None  # falsifié
    assert Authenticator("veille", "Str0ng!Pass", secret="autre").verify(token, now=1001) is None


def test_basic_header_and_throttle():
    auth = Authenticator("veille", "Str0ng!Pass")
    good = "Basic " + base64.b64encode(b"veille:Str0ng!Pass").decode()
    assert auth.basic(good) and not auth.basic("Basic " + base64.b64encode(b"veille:nope").decode())
    throttle = Throttle(max_failures=2, lock_seconds=60)
    throttle.failure("1.2.3.4", now=10)
    assert throttle.locked_for("1.2.3.4", now=11) == 0
    throttle.failure("1.2.3.4", now=12)
    assert 0 < throttle.locked_for("1.2.3.4", now=13) <= 60
    assert throttle.locked_for("5.6.7.8", now=13) == 0
    assert throttle.locked_for("1.2.3.4", now=12 + 61) == 0
