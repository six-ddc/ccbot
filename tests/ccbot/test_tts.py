"""Tests for TTS module."""

from unittest.mock import MagicMock, patch

import pytest

from ccbot.tts import is_tts_enabled, toggle_tts


@pytest.fixture
def mock_config():
    with patch.object(
        __import__("ccbot.tts", fromlist=["config"]).config,
        "tts_enabled",
        True,
        create=True,
    ), patch.object(
        __import__("ccbot.tts", fromlist=["config"]).config,
        "tts_auto",
        False,
        create=True,
    ), patch.object(
        __import__("ccbot.tts", fromlist=["config"]).config,
        "tts_voice",
        "es-ES-ElviraNeural",
        create=True,
    ):
        from ccbot import tts

        tts._per_user_tts.clear()
        yield tts.config


class TestTTSToggle:
    def test_toggle_on_by_default_tts_auto_false(self, mock_config):
        assert is_tts_enabled(12345) is False

    def test_toggle_on(self, mock_config):
        new_state = toggle_tts(12345)
        assert new_state is True
        assert is_tts_enabled(12345) is True

    def test_toggle_off(self, mock_config):
        toggle_tts(12345)
        new_state = toggle_tts(12345)
        assert new_state is False
        assert is_tts_enabled(12345) is False

    def test_per_user_isolation(self, mock_config):
        toggle_tts(100)
        assert is_tts_enabled(100) is True
        assert is_tts_enabled(200) is False


class TestTTSGlobalDisabled:
    def test_global_disabled_ignores_per_user(self):
        with patch(
            "ccbot.tts.config",
            MagicMock(tts_enabled=False, tts_auto=False, tts_voice="test"),
        ):
            from ccbot import tts

            tts._per_user_tts.clear()
            assert is_tts_enabled(12345) is False


class TestCleanTextForTTS:
    def test_strips_emojis(self):
        from ccbot.tts import clean_text_for_tts

        result = clean_text_for_tts("Hola 👋 ¿cómo estás? 😊")
        assert "👋" not in result
        assert "😊" not in result
        assert "Hola" in result
        assert "cómo estás" in result

    def test_strips_markdown(self):
        from ccbot.tts import clean_text_for_tts

        result = clean_text_for_tts("## Título **negrita** y `código`")
        assert "##" not in result
        assert "**" not in result
        assert "`" not in result
        assert "Título" in result
        assert "negrita" in result

    def test_strips_arrows_and_symbols(self):
        from ccbot.tts import clean_text_for_tts

        result = clean_text_for_tts("⚠️ Error → solución ✅")
        assert "⚠️" not in result
        assert "→" not in result
        assert "✅" not in result
        assert "Error" in result
        assert "solución" in result

    def test_keeps_normal_punctuation(self):
        from ccbot.tts import clean_text_for_tts

        result = clean_text_for_tts("¡Hola! ¿Qué tal? Bien, gracias.")
        assert "¡" in result
        assert "!" in result
        assert "¿" in result
        assert "?" in result
        assert "." in result

    def test_empty_after_clean(self):
        from ccbot.tts import clean_text_for_tts

        result = clean_text_for_tts("⚠️⚡🔥")
        assert result == ""
