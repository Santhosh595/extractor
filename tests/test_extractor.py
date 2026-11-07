"""Unit tests for extractor. Stdlib unittest — run: python -m unittest discover -s tests -v"""

import unittest
from unittest.mock import patch

from extractor import (
    PROVIDERS,
    decrypt,
    encrypt,
    fetch_all,
    format_all,
    format_models,
    is_free,
    model_key,
)


def _m(id_, source="OpenRouter", **kw):
    base = {"source": source, "id": id_, "name": id_.title(),
            "context_length": 128000, "modality": "text->text",
            "pricing": {"prompt": "0", "completion": "0"},
            "details": "test", "key_required": False}
    base.update(kw)
    return base


class TestIsFree(unittest.TestCase):
    def test_zero_strings_are_free(self):
        self.assertTrue(is_free({"pricing": {"prompt": "0", "completion": "0"}}))

    def test_zero_floats_are_free(self):
        self.assertTrue(is_free({"pricing": {"prompt": 0, "completion": 0.0}}))

    def test_cheap_is_not_free(self):
        self.assertFalse(is_free({"pricing": {"prompt": "0.01", "completion": "0.01"}}))

    def test_one_side_paid_is_not_free(self):
        self.assertFalse(is_free({"pricing": {"prompt": "0", "completion": "0.05"}}))

    def test_missing_pricing_is_not_free(self):
        self.assertFalse(is_free({}))
        self.assertFalse(is_free({"pricing": {}}))
        self.assertFalse(is_free({"pricing": {"prompt": "abc", "completion": "0"}}))


class TestSecrets(unittest.TestCase):
    def test_roundtrip(self):
        blob = encrypt("correct horse", '{"token": "123:ABC", "chat_id": "42"}')
        self.assertNotIn("123:ABC", blob)  # plaintext must not appear
        self.assertEqual(decrypt("correct horse", blob), '{"token": "123:ABC", "chat_id": "42"}')

    def test_wrong_password_gives_garbage_or_crashes(self):
        blob = encrypt("right", "secret data")
        try:
            out = decrypt("wrong", blob)
            self.assertNotEqual(out, "secret data")
        except Exception:
            pass  # crash is also acceptable

    def test_different_salts_give_different_ciphertext(self):
        a = encrypt("pw", "same input")
        b = encrypt("pw", "same input")
        self.assertNotEqual(a, b)  # random salt per encryption


class TestFormat(unittest.TestCase):
    def test_format_lists_source_model_details(self):
        out = format_models([_m("a/b:free")], "OpenRouter")
        self.assertIn("OpenRouter", out)
        self.assertIn("a/b:free", out)
        self.assertIn("ctx=128000", out)
        self.assertIn("text->text", out)

    def test_format_key_required_flag(self):
        out = format_models([_m("x/y", key_required=True)])
        self.assertIn("[key required]", out)

    def test_format_all_totals_and_caps(self):
        by = {"P1": [_m(f"m{i}/x") for i in range(300)], "P2": [_m("z/z", source="P2")]}
        out = format_all(by)
        self.assertIn("301 total", out)
        self.assertLessEqual(len(out), 4000)

    def test_format_models_empty_is_empty(self):
        self.assertEqual(format_models([]), "")


class TestProviders(unittest.TestCase):
    def test_providers_have_required_fields(self):
        for p in PROVIDERS:
            self.assertIn("name", p)
            self.assertIn("url", p)
            self.assertTrue(callable(p["norm"]))

    def test_fetch_all_survives_one_provider_down(self):
        """A dead source must not kill the cycle — others still return."""
        fake_bodies = {
            "OpenRouter": {"data": [{"id": "a/b:free", "name": "A", "context_length": 1000,
                                     "architecture": {"modality": "text->text"},
                                     "pricing": {"prompt": "0", "completion": "0"},
                                     "created": 1, "reasoning": {}, "hugging_face_id": ""}]},
            "Pollinations": [{"name": "openai-fast", "description": "d", "tier": "anonymous"}],
            "Sambanova": {"data": [{"id": "m1", "context_length": 1000, "owned_by": "o",
                                    "max_completion_tokens": 100, "pricing": {}}]},
            "DeepInfra": {"data": [{"id": "d/1", "owned_by": "o", "created": 0, "metadata": {}}]},
            "Featherless": {"data": []},
        }
        def fake_get(url, timeout=30):
            for key, body in fake_bodies.items():
                if key.lower() in url.lower():
                    return body
            raise RuntimeError("no such provider")
        with patch("extractor._http_get", side_effect=fake_get):
            out = fetch_all()
        self.assertEqual(len(out["OpenRouter"]), 1)
        self.assertEqual(out["OpenRouter"][0]["source"], "OpenRouter")
        self.assertEqual(out["Pollinations"][0]["id"], "openai-fast")
        self.assertEqual(len(out["Sambanova"]), 1)
        self.assertTrue(out["DeepInfra"][0]["key_required"])

    def test_fetch_all_one_down_others_ok(self):
        def flaky(url, timeout=30):
            if "openrouter" in url:
                raise RuntimeError("provider down")
            if "pollinations" in url:
                return [{"name": "m", "description": "", "tier": "anonymous"}]
            return {"data": []}
        with patch("extractor._http_get", side_effect=flaky):
            out = fetch_all()
        self.assertEqual(out["OpenRouter"], [])
        self.assertEqual(len(out["Pollinations"]), 1)

    def test_model_key_scopes_by_provider(self):
        self.assertNotEqual(model_key(_m("same-id", "OpenRouter")),
                            model_key(_m("same-id", "Sambanova")))
        self.assertEqual(model_key(_m("x", "OpenRouter")), "OpenRouter:x")


if __name__ == "__main__":
    unittest.main()
