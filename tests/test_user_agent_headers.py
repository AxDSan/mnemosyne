"""Regression coverage for explicit application User-Agent headers."""

import builtins
import io
import json
import sys
from unittest.mock import MagicMock, patch

from mnemosyne.core import embeddings, local_llm


class EmbeddingResponse:
    def __init__(self, payload):
        self.body = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body.read()


def test_embed_api_includes_application_user_agent(monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_EMBEDDING_API_URL", "http://127.0.0.1:11435/v1")
    result = EmbeddingResponse({"data": [{"embedding": [0.25, 0.75]}]})

    with patch("urllib.request.urlopen", return_value=result) as request:
        embeddings._embed_api(["user agent"])

    assert request.call_args.args[0].headers["User-agent"] == "Mnemosyne/3.16.0"


def test_remote_httpx_includes_application_user_agent(monkeypatch):
    monkeypatch.setattr(local_llm, "LLM_BASE_URL", "http://test-server/v1")
    monkeypatch.setattr(local_llm, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(local_llm, "LLM_MAX_TOKENS", 128)

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "summary"}}]
    }
    mock_client.post.return_value = mock_response
    mock_client.__enter__ = lambda value: value
    mock_client.__exit__ = lambda *_args: None

    mock_httpx = MagicMock()
    mock_httpx.Client.return_value = mock_client
    original_import = builtins.__import__

    def import_httpx(name, *args, **kwargs):
        if name == "httpx":
            return mock_httpx
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", import_httpx):
        assert local_llm._call_remote_llm_with_model("prompt", "model")[0] == "summary"

    assert mock_client.post.call_args.kwargs["headers"]["User-Agent"] == "Mnemosyne/3.16.0"


def test_remote_urllib_fallback_includes_application_user_agent(monkeypatch):
    monkeypatch.setattr(local_llm, "LLM_BASE_URL", "http://test-server/v1")
    monkeypatch.setattr(local_llm, "LLM_API_KEY", "")
    monkeypatch.setattr(local_llm, "LLM_MAX_TOKENS", 128)

    class Response:
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "summary"}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    with patch.dict(sys.modules, {"httpx": None}):
        with patch("urllib.request.urlopen", return_value=Response()) as request:
            assert local_llm._call_remote_llm_with_model("prompt", "model")[0] == "summary"

    assert request.call_args.args[0].headers["User-agent"] == "Mnemosyne/3.16.0"
