"""Unit tests for extractor. Stdlib unittest — run: python -m unittest discover -s tests -v"""

import unittest

from extractor import decrypt, encrypt, format_models, is_free


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
    def test_format_lists_ids(self):
        out = format_models([{"id": "a/b:free", "name": "Model A"}, {"id": "c/d:free", "name": "Model B"}])
        self.assertIn("a/b:free", out)
        self.assertIn("Model A", out)
        self.assertIn("2", out)  # count in header

    def test_format_truncates_long(self):
        models = [{"id": f"m{i}/x", "name": "N" * 100} for i in range(200)]
        self.assertLessEqual(len(format_models(models)), 4000)


if __name__ == "__main__":
    unittest.main()
