"""Голосовые: бот находит распознаватель сам, без правки настроек."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.voice import VoiceConfig, build_transcriber, discover, find_ffmpeg

from .test_handlers import BotTestCase


def make_home(tmp: str, binary: str = "whisper-cli", models=("ggml-small.bin",)) -> Path:
    home = Path(tmp) / "whisper"
    (home / "Release").mkdir(parents=True)
    if binary:
        path = home / "Release" / binary
        path.write_bytes(b"x")
    for name in models:
        (home / name).write_bytes(b"y" * 100)
    return home


class DiscoverTest(unittest.TestCase):
    def test_finds_binary_and_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp)
            binary, model = discover(home)
            self.assertTrue(binary.endswith("whisper-cli"))
            self.assertTrue(model.endswith("ggml-small.bin"))

    def test_bigger_model_wins(self):
        """Их может лежать несколько: крупная — значит точнее."""
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, models=())
            (home / "ggml-tiny.bin").write_bytes(b"y" * 10)
            (home / "ggml-small.bin").write_bytes(b"y" * 500)

            _, model = discover(home)
            self.assertTrue(model.endswith("ggml-small.bin"))

    def test_nothing_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(discover(Path(tmp) / "нет"), ("", ""))

    def test_half_installed_is_not_enough(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, binary="", models=("ggml-small.bin",))
            binary, model = discover(home)
            self.assertEqual(binary, "")
            self.assertTrue(model)

            self.assertFalse(VoiceConfig().with_discovered(home).enabled)

    def test_settings_win_over_discovery(self):
        """Прописанное руками важнее найденного: человек знает, чего хочет."""
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp)
            config = VoiceConfig(binary="/свой/whisper", model="/своя/model.bin")
            self.assertEqual(config.with_discovered(home).binary, "/свой/whisper")

    def test_ffmpeg_from_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp)
            (home / "bin").mkdir()
            (home / "bin" / "ffmpeg").write_bytes(b"z")
            self.assertTrue(find_ffmpeg(home))

    def test_disabled_without_anything(self):
        self.assertFalse(build_transcriber(VoiceConfig()).ready)
        self.assertFalse(build_transcriber(None).ready)


class VoiceCommandTest(BotTestCase):
    async def test_says_what_is_missing_and_how_to_fix(self):
        answer = await self.send("/voice")
        self.assertIn("не разбираю", answer)
        self.assertIn("setup-voice.bat", answer)
        self.assertIn("❌", answer)

    async def test_explains_the_price(self):
        """Расшифровка идёт на слабом ноутбуке — про это надо сказать заранее."""
        answer = await self.send("/voice")
        self.assertIn("на твоём компьютере", answer)


class SetupScriptTest(unittest.TestCase):
    """Скрипт установки — тоже часть бота, пусть будет цел."""

    def test_scripts_exist_and_are_windows_friendly(self):
        root = Path(__file__).resolve().parent.parent
        bat = root / "tools" / "setup-voice.bat"
        ps1 = root / "tools" / "setup-voice.ps1"
        sh = root / "tools" / "setup-voice.sh"

        for path in (bat, ps1, sh):
            self.assertTrue(path.exists(), path.name)

        # cmd понимает русский только после chcp, и только в CRLF-файле
        head = bat.read_bytes().splitlines()[:4]
        self.assertTrue(any(b"chcp 65001" in line for line in head))
        self.assertIn(b"\r\n", bat.read_bytes())
        self.assertIn(b"\r\n", ps1.read_bytes())
        self.assertNotIn(b"\r\n", sh.read_bytes())

    def test_powershell_checks_what_it_downloaded(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "tools" / "setup-voice.ps1").read_text(encoding="utf-8")
        # молча оставить битую модель — худшее, что может сделать установщик
        self.assertIn("50MB", text)
        self.assertIn("--help", text)


if __name__ == "__main__":
    unittest.main()
