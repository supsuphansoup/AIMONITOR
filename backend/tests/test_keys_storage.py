"""Key storage survives failures and independent desktop/backend writers."""
import json
import multiprocessing
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

import pytest

from backend.app.services import secret_store
from backend.app.services.api_key_manager import KNOWN_PROVIDERS, APIKeyManager, api_key_manager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    for info in KNOWN_PROVIDERS:
        for name in info["env_vars"]:
            monkeypatch.delenv(name, raising=False)
    if secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI:
        secrets = {}

        def seal(secret):
            token = str(len(secrets))
            secrets[token] = secret
            return "test", token

        monkeypatch.setattr(secret_store, "AVAILABLE_SCHEME", "test")
        monkeypatch.setattr(secret_store, "seal", seal)
        monkeypatch.setattr(secret_store, "unseal", lambda scheme, token: secrets.get(token))
    path = tmp_path / "keys.json"
    monkeypatch.setattr(api_key_manager, "filepath", path)
    return APIKeyManager(path)


@pytest.mark.parametrize("failure", ["replace", "fsync"])
def test_failed_atomic_replace_preserves_previous_ciphertext(manager, monkeypatch, failure):
    manager.add_key("groq", "synthetic-old", "기존")
    previous = manager.filepath.read_bytes()

    def fail(*args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(os, failure, fail)
    with pytest.raises(ValueError):
        manager.add_key("groq", "synthetic-new", "새 키")
    assert manager.filepath.read_bytes() == previous
    assert sorted(p.name for p in manager.filepath.parent.iterdir()) == ["keys.json", "keys.json.lock"]


def test_independent_managers_preserve_concurrent_updates(manager, monkeypatch):
    other = APIKeyManager(manager.filepath)
    ready, release = threading.Event(), threading.Event()
    real_save = APIKeyManager._save_raw

    def delayed_save(instance, data):
        if instance is manager:
            ready.set()
            assert release.wait(3)
        real_save(instance, data)

    monkeypatch.setattr(APIKeyManager, "_save_raw", delayed_save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager.add_key, "groq", "synthetic-one", "하나")
        assert ready.wait(3)
        second = pool.submit(other.add_key, "groq", "synthetic-two", "둘")
        try:
            second.result(timeout=0.1)
        except TimeoutError:
            pass
        finally:
            release.set()
        first.result(timeout=3)
        second.result(timeout=3)
    assert len(manager.list_keys("groq")) == 2


@pytest.mark.parametrize("content", [
    '{"groq":', '[]', '{"groq":null}', '{"groq":[]}', '{"groq":{"id":null}}',
    '{"groq":{"id":{"label":null}}}', '{"groq":{"id":{"status":null}}}',
    '{"groq":{"id":{"scheme":null}}}', '{"groq":{"id":{"sealed":null}}}',
    '{"groq":' + '[' * 1100 + ']' * 1100 + '}',
], ids=["json", "root", "null-provider", "array-provider", "null-entry", "null-label", "null-status",
        "null-scheme", "null-sealed", "deep-json"])
def test_corrupt_store_is_preserved_and_listed_safely(manager, client, content):
    manager.filepath.write_text(content, encoding="utf-8")
    before = manager.filepath.read_bytes()
    with pytest.raises(ValueError):
        manager.add_key("groq", "synthetic-new", "새 키")
    keys = manager.list_keys("groq")
    assert keys and keys[0]["status"] == "storage_error" and not keys[0]["has_key"]
    response = client.get("/api/v1/keys")
    assert response.status_code == 200
    assert next(k for k in response.json() if k["provider"] == "groq")["status"] == "storage_error"
    assert manager.filepath.read_bytes() == before


def test_unreadable_secret_has_explicit_status(manager, monkeypatch):
    item = manager.add_key("groq", "synthetic-old", "기존")
    monkeypatch.setattr(secret_store, "unseal", lambda *args: None)
    view = manager.list_keys("groq")[0]
    assert view["key_id"] == item["key_id"]
    assert view["status"] == "unreadable" and not view["has_key"]


def test_non_windows_legacy_file_does_not_block_environment(manager, monkeypatch):
    manager.filepath.write_text(json.dumps({"groq": {"key": "synthetic-legacy"}}), encoding="utf-8")
    before = manager.filepath.read_bytes()
    monkeypatch.setattr(secret_store, "AVAILABLE_SCHEME", "unavailable")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-env")
    assert manager.get_registered_key("openai") == "synthetic-env"
    assert manager.get_key("openai", "env") == "synthetic-env"
    assert manager.get_registered_key("groq") is None
    assert manager.list_keys("groq")[0]["status"] == "unreadable"
    with pytest.raises(ValueError):
        manager.add_key("groq", "synthetic-new")
    assert manager.filepath.read_bytes() == before


@pytest.mark.parametrize("secret", ["abc\nxyz", "abc\rxyz", "abc\x00xyz", "abc\txyz", "é-key", "x" * 4097],
                         ids=["lf", "cr", "nul", "tab", "non-ascii", "too-long"])
def test_invalid_header_or_oversized_key_is_rejected(manager, secret):
    with pytest.raises(ValueError):
        manager.add_key("groq", secret)
    assert not manager.filepath.exists()


def test_lock_timeout_is_controlled_and_readable(manager, monkeypatch):
    real_lock = secret_store.lock_file
    monkeypatch.setattr(secret_store, "lock_file", lambda path: real_lock(path, timeout=0.04))
    with real_lock(manager.filepath.with_name(manager.filepath.name + ".lock")):
        with pytest.raises(ValueError, match="busy"):
            manager.add_key("groq", "synthetic-new")
        assert manager.list_keys("groq")[0]["status"] == "storage_error"


def test_storage_read_error_does_not_allow_overwrite(manager, monkeypatch):
    manager.add_key("groq", "synthetic-old")
    previous = manager.filepath.read_bytes()
    real_read = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == manager.filepath:
            raise PermissionError("synthetic permission denied")
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(ValueError):
        manager.add_key("groq", "synthetic-new")
    assert manager.list_keys("groq")[0]["status"] == "storage_error"
    assert manager.filepath.read_bytes() == previous


def _process_add(path, prefix, start):
    start.wait(5)
    for index in range(4):
        APIKeyManager(Path(path)).add_key("groq", f"synthetic-{prefix}-{index}", f"{prefix} {index}")


@pytest.mark.skipif(secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI, reason="real DPAPI")
def test_two_process_writers_preserve_all_keys(manager):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    processes = [context.Process(target=_process_add, args=(str(manager.filepath), str(i), start))
                 for i in range(2)]
    try:
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(3)
    assert len(manager.list_keys("groq")) == 8
