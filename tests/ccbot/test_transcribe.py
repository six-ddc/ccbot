"""Unit tests for transcribe — voice-to-text via Deepgram API."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ccbot import transcribe


DEEPGRAM_RESPONSE = {
    "results": {
        "channels": [
            {
                "alternatives": [
                    {"transcript": "Hello world", "confidence": 0.99}
                ]
            }
        ]
    }
}


@pytest.fixture(autouse=True)
def _reset_client():
    """Ensure each test starts with a fresh client."""
    transcribe._client = None
    yield
    transcribe._client = None


@pytest.fixture
def mock_config():
    """Patch config with test values."""
    with patch.object(transcribe, "config") as cfg:
        cfg.deepgram_api_key = "test-deepgram-key"
        yield cfg


def _mock_response(*, json_data: dict, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response."""
    request = httpx.Request("POST", transcribe.DEEPGRAM_API_URL)
    resp = httpx.Response(status_code=status_code, json=json_data, request=request)
    return resp


class TestTranscribeVoice:
    @pytest.mark.asyncio
    async def test_success(self, mock_config):
        resp = _mock_response(json_data=DEEPGRAM_RESPONSE)
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=resp
        ) as mock_post:
            result = await transcribe.transcribe_voice(b"fake-ogg-data")

        assert result == "Hello world"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "Token test-deepgram-key" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_empty_transcription_raises(self, mock_config):
        data = {"results": {"channels": [{"alternatives": [{"transcript": "", "confidence": 0.0}]}]}}
        resp = _mock_response(json_data=data)
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=resp
        ):
            with pytest.raises(ValueError, match="Empty transcription"):
                await transcribe.transcribe_voice(b"fake-ogg-data")

    @pytest.mark.asyncio
    async def test_unexpected_structure_raises(self, mock_config):
        resp = _mock_response(json_data={"metadata": {}})
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=resp
        ):
            with pytest.raises(ValueError, match="Unexpected Deepgram response"):
                await transcribe.transcribe_voice(b"fake-ogg-data")

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_config):
        resp = _mock_response(json_data={"error": "Unauthorized"}, status_code=401)
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=resp
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await transcribe.transcribe_voice(b"fake-ogg-data")


class TestCloseClient:
    @pytest.mark.asyncio
    async def test_close_client_when_open(self):
        transcribe._client = httpx.AsyncClient()
        assert transcribe._client is not None
        await transcribe.close_client()
        assert transcribe._client is None

    @pytest.mark.asyncio
    async def test_close_client_when_none(self):
        assert transcribe._client is None
        await transcribe.close_client()
        assert transcribe._client is None
