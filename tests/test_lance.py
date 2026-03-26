"""Tests for steps/_lance.py credential validation and URI construction."""

import hashlib

import pytest

from steps._lance import LanceStore


def test_lance_store_missing_credentials(monkeypatch):
    monkeypatch.delenv("TOS_ACCESS_KEY", raising=False)
    monkeypatch.delenv("TOS_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TOS 凭证缺失"):
        LanceStore({"tos": {}})


def test_lance_store_env_vars(monkeypatch):
    monkeypatch.setenv("TOS_ACCESS_KEY", "test_ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "test_sk")
    store = LanceStore({"tos": {"bucket": "mybucket", "base_path": "my/path"}})
    assert store._storage_options["access_key_id"] == "test_ak"
    assert store._storage_options["secret_access_key"] == "test_sk"


def test_lance_store_config_fallback(monkeypatch):
    monkeypatch.delenv("TOS_ACCESS_KEY", raising=False)
    monkeypatch.delenv("TOS_SECRET_KEY", raising=False)
    store = LanceStore({
        "tos": {
            "access_key": "cfg_ak",
            "secret_key": "cfg_sk",
            "bucket": "mybucket",
        }
    })
    assert store._storage_options["access_key_id"] == "cfg_ak"


def test_table_uri(monkeypatch):
    monkeypatch.setenv("TOS_ACCESS_KEY", "ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "sk")
    store = LanceStore({
        "tos": {"bucket": "mybucket", "base_path": "my/path", "endpoint": "tos.example.com"}
    })
    assert store._table_uri("optimization_history") == "s3://mybucket/my/path/optimization_history.lance"
    assert store._table_uri("prompt_templates") == "s3://mybucket/my/path/prompt_templates.lance"


def test_sha256_consistency():
    content = "This is a test prompt template"
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert len(h) == 64
    # Deterministic
    assert h == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_base_path_strip(monkeypatch):
    monkeypatch.setenv("TOS_ACCESS_KEY", "ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "sk")
    store = LanceStore({"tos": {"bucket": "b", "base_path": "/leading/trailing/"}})
    assert store._table_uri("t") == "s3://b/leading/trailing/t.lance"
