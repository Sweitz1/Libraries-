"""The media_text plugins (image OCR, audio and video transcription). They call outside tools, so the
tools are mocked here: CI needs no tesseract, whisper or ffmpeg. The tests check the command built, the
output handling, and the clear message when a tool is missing."""
import importlib.util
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location("media_text_plugin", os.path.join(ROOT, "plugins", "media_text.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MediaTextTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, name):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(b"\x00")
        return p

    def completed(self, stdout=b"", returncode=0, stderr=b""):
        return mock.Mock(stdout=stdout, stderr=stderr, returncode=returncode)

    def test_image_ocr_builds_the_command_and_returns_text(self):
        img = self.touch("receipt.jpg")
        out = os.path.join(self.tmp.name, "receipt.txt")
        with mock.patch.object(self.m.shutil, "which", lambda t: "/usr/bin/" + t), \
                mock.patch.object(self.m, "_run", return_value=self.completed(b"Total: 42.00\n")) as run:
            r = self.m.image_ocr(img, out=out, lang="eng")
        self.assertEqual(run.call_args[0][0], ["tesseract", img, "stdout", "-l", "eng"])
        self.assertTrue(r.ok)
        self.assertEqual(r.data["tool"], "tesseract")
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "Total: 42.00")

    def test_missing_tool_is_explained_not_crashed(self):
        img = self.touch("x.png")
        with mock.patch.object(self.m.shutil, "which", lambda t: None):
            r = self.m.image_ocr(img)
        self.assertFalse(r.ok)
        self.assertIn("tesseract", r.text)
        self.assertIn("apt install tesseract-ocr", r.text)

    def test_audio_transcribe_reads_whispers_txt_output(self):
        audio = self.touch("talk.mp3")

        def fake_run(cmd, timeout=1800):
            # whisper writes <stem>.txt into its --output_dir
            d = cmd[cmd.index("--output_dir") + 1]
            with open(os.path.join(d, "talk.txt"), "w", encoding="utf-8") as f:
                f.write("hello from the recording")
            self.assertIn("--language", cmd)
            self.assertEqual(cmd[cmd.index("--model") + 1], "small")
            return self.completed(returncode=0)

        with mock.patch.object(self.m.shutil, "which", lambda t: "/usr/bin/" + t), \
                mock.patch.object(self.m, "_run", side_effect=fake_run):
            r = self.m.audio_transcribe(audio, model="small", language="en")
        self.assertTrue(r.ok)
        self.assertIn("hello from the recording", r.text)
        self.assertEqual(r.data["tool"], "whisper (small)")

    def test_video_runs_ffmpeg_then_whisper(self):
        video = self.touch("clip.mp4")
        calls = []

        def fake_run(cmd, timeout=1800):
            calls.append(cmd[0])
            if cmd[0] == "ffmpeg":
                wav = cmd[-1]
                with open(wav, "wb") as f:
                    f.write(b"RIFF")  # pretend audio was extracted
                return self.completed(returncode=0)
            d = cmd[cmd.index("--output_dir") + 1]  # whisper on audio.wav
            with open(os.path.join(d, "audio.txt"), "w", encoding="utf-8") as f:
                f.write("spoken words in the video")
            return self.completed(returncode=0)

        with mock.patch.object(self.m.shutil, "which", lambda t: "/usr/bin/" + t), \
                mock.patch.object(self.m, "_run", side_effect=fake_run):
            r = self.m.video_transcribe(video)
        self.assertEqual(calls, ["ffmpeg", "whisper"])
        self.assertTrue(r.ok)
        self.assertIn("spoken words in the video", r.text)

    def test_wrong_extension_and_missing_file(self):
        with self.assertRaises(ValueError):
            self.m.image_ocr(self.touch("notes.txt"))
        with self.assertRaises(ValueError):
            self.m.audio_transcribe(os.path.join(self.tmp.name, "nope.mp3"))

    def test_tool_failure_is_reported(self):
        img = self.touch("x.png")
        with mock.patch.object(self.m.shutil, "which", lambda t: "/usr/bin/" + t), \
                mock.patch.object(self.m, "_run", return_value=self.completed(returncode=1, stderr=b"boom")):
            r = self.m.image_ocr(img)
        self.assertFalse(r.ok)
        self.assertIn("boom", r.text)

    def test_registration(self):
        from libra.skills import SkillRegistry

        reg = SkillRegistry()
        got = reg.load_plugins([os.path.join(ROOT, "plugins")], log=None)
        self.assertEqual({"image_ocr", "audio_transcribe", "video_transcribe"} & set(got),
                         {"image_ocr", "audio_transcribe", "video_transcribe"})
        # unaccepted plugin: read label held as write until the file is accepted
        self.assertEqual((reg.get("image_ocr").risk, reg.get("image_ocr").claimed_risk), ("write", "read"))


if __name__ == "__main__":
    unittest.main()
