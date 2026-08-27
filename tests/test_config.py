from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from nanominer_k3.config import ConfigurationError, KimiSettings
from nanominer_k3.profiles import load_profile


class KimiSettingsTests(TestCase):
    def test_missing_key_is_allowed_only_for_doctor(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = KimiSettings.from_env(require_api_key=False)
            self.assertFalse(settings.public_summary()["api_key_configured"])
            with self.assertRaises(ConfigurationError):
                KimiSettings.from_env(require_api_key=True)

    def test_core_model_is_pinned_to_kimi_k3(self) -> None:
        with patch.dict(
            os.environ,
            {"MOONSHOT_API_KEY": "test", "KIMI_MODEL": "gpt-4o"},
            clear=True,
        ):
            with self.assertRaisesRegex(ConfigurationError, "pins the core agent"):
                KimiSettings.from_env()

    def test_kimi_code_endpoint_uses_k3_model_id(self) -> None:
        settings = KimiSettings(
            api_key="test",
            base_url="https://api.kimi.com/coding/v1",
            model="k3",
        )
        settings.validate()
        self.assertEqual("kimi_code", settings.service)

    def test_kimi_code_model_defaults_from_endpoint(self) -> None:
        with patch.dict(
            os.environ,
            {
                "KIMI_API_KEY": "test",
                "KIMI_BASE_URL": "https://api.kimi.com/coding/v1",
            },
            clear=True,
        ):
            settings = KimiSettings.from_env()
        self.assertEqual("k3", settings.model)

    def test_public_summary_does_not_leak_key(self) -> None:
        settings = KimiSettings(api_key="top-secret")
        rendered = repr(settings.public_summary())
        self.assertNotIn("top-secret", rendered)
        self.assertEqual("china", settings.public_summary()["region"])

    def test_api_key_cannot_be_sent_to_an_unapproved_base_url(self) -> None:
        settings = KimiSettings(
            api_key="top-secret",
            base_url="https://attacker.example/v1",
        )
        with self.assertRaisesRegex(ConfigurationError, "official Kimi endpoint"):
            settings.validate()

    def test_built_in_pe_profile_loads(self) -> None:
        profile = load_profile("pe_crystal")
        self.assertEqual("pe_crystal", profile.profile_id)
        self.assertIn("space_group_raw", profile.instructions)
