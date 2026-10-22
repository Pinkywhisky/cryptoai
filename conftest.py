"""Keep pytest independent from personal data, credentials and the network."""

import os
from pathlib import Path
import socket
import sqlite3
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit

import pytest


_ISOLATION = pytest.StashKey()


def pytest_configure(config):
    # This hook runs before test collection, including imports of main.app.
    directory = tempfile.TemporaryDirectory(prefix="cryptoai-tests-")
    test_root = Path(directory.name).resolve()
    patches = pytest.MonkeyPatch()
    config.stash[_ISOLATION] = (directory, patches)

    patches.setenv("CRYPTOAI_DB_PATH", str(test_root / "history.db"))
    patches.setenv("PYTHON_DOTENV_DISABLED", "1")
    patches.setenv("BINANCE_API_KEY", "")
    patches.setenv("BINANCE_API_SECRET", "")
    patches.setenv("BINANCE_USE_TESTNET", "false")
    patches.setenv("MIN_ACTIVE_POSITION_VALUE_USDC", "1.00")
    from pwdlib import PasswordHash
    patches.setenv("CRYPTOAI_AUTH_PASSWORD_HASH", PasswordHash.recommended().hash("test-only-cryptoai-password-2026"))
    patches.setenv("CRYPTOAI_ALLOWED_ORIGINS", "http://localhost:8000")
    patches.setenv("CRYPTOAI_SESSION_SECONDS", "3600")
    patches.setenv("CRYPTOAI_ALLOW_DESTRUCTIVE", "0")
    patches.setenv("CRYPTOAI_ENV", "development")
    patches.setenv("CRYPTOAI_TRUSTED_PROXIES", "")


    if config.option.basetemp is None:
        config.option.basetemp = str(test_root / "pytest")
    pytest_root = Path(config.option.basetemp).resolve()
    system_temp = Path(tempfile.gettempdir()).resolve()
    if pytest_root == system_temp or not pytest_root.is_relative_to(system_temp):
        raise pytest.UsageError("--basetemp must be a subdirectory of the system temporary directory")

    def deny_network(*args, **kwargs):
        raise AssertionError("Network access is disabled in tests; mock the external data")

    patches.setattr(socket, "getaddrinfo", deny_network)
    for method in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, method):
            patches.setattr(socket.socket, method, deny_network)

    connect = sqlite3.connect

    def connect_temporary(database, *args, **kwargs):
        name = os.fsdecode(database)
        if name not in ("", ":memory:"):
            if name.startswith("file:"):
                uri = urlsplit(name)
                if parse_qs(uri.query).get("mode") == ["memory"]:
                    return connect(database, *args, **kwargs)
                name = unquote(uri.path)
            target = Path(name).resolve()
            if not any(target.is_relative_to(root) for root in (test_root, pytest_root)):
                raise AssertionError("Tests may only open SQLite databases in temporary directories")
        return connect(database, *args, **kwargs)

    patches.setattr(sqlite3, "connect", connect_temporary)


@pytest.fixture(autouse=True)
def empty_default_database():
    # Modules retain this temporary path in their default arguments.
    from services.history import reset_database
    from services.binance_alpha import clear_alpha_cache

    clear_alpha_cache()

    reset_database(db_path=Path(os.environ["CRYPTOAI_DB_PATH"]))
    # Quota bans survive production resets; each isolated test starts fresh.
    with sqlite3.connect(Path(os.environ["CRYPTOAI_DB_PATH"])) as connection:
        connection.execute("DELETE FROM binance_api_state")


def pytest_unconfigure(config):
    state = config.stash.get(_ISOLATION, None)
    if state is not None:
        directory, patches = state
        patches.undo()
        directory.cleanup()


@pytest.fixture
def enable_destructive(monkeypatch):
    # Explicit authorization for the two legacy admin regression tests only.
    from dataclasses import replace
    import main
    monkeypatch.setattr(main.app.state.auth, "settings",
                        replace(main.app.state.auth.settings, allow_destructive=True))
