"""sound_events: audio frontend and event merging (no model needed, except the last test)."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sound_events as se  # noqa: E402


class TestFrontend(unittest.TestCase):
    def test_mel_matrix_shape_and_dc_row(self):
        m = se.mel_matrix()
        self.assertEqual(m.shape, (257, 64))
        self.assertTrue(np.all(m[0] == 0))           # DC bin carries no weight
        self.assertTrue(np.all(m.max(0) > 0))        # every mel band has some bins

    def test_one_second_gives_one_patch(self):
        mel = se.log_mel(np.zeros(16000, np.float32))
        self.assertEqual(mel.shape, (98, 64))
        self.assertEqual(se.patches(mel).shape, (1, 96, 64))

    def test_short_audio_is_padded_to_one_patch(self):
        self.assertEqual(se.patches(se.log_mel(np.zeros(800, np.float32))).shape, (1, 96, 64))


class TestEvents(unittest.TestCase):
    LABELS = ["Speech", "Boom", "Whoosh"]

    def _run(self, col, **kw):
        sc = np.zeros((len(col), 3), np.float32)
        sc[:, 1] = col
        sc[:, 0] = 0.9
        times = np.arange(len(col)) * se.STEP
        return se.events(times, sc, self.LABELS, **kw)

    def test_one_missed_patch_does_not_split(self):
        rows = self._run([0, 0.5, 0, 0.6, 0, 0, 0.4])
        self.assertEqual([(r["start"], r["peak"]) for r in rows], [(0.48, 0.6), (2.88, 0.4)])

    def test_background_hidden_by_default(self):
        self.assertNotIn("Speech", {r["label"] for r in self._run([0.5])})
        self.assertIn("Speech", {r["label"] for r in self._run([0.5], hide=set())})


@unittest.skipUnless((se.MODEL_DIR / "yamnet.onnx").exists(), "YAMNet model not downloaded")
class TestModel(unittest.TestCase):
    def test_hears_a_tone_as_a_tone(self):
        sess, labels = se.load_model()
        t = np.arange(16000 * 2) / 16000
        wave = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        x = se.patches(se.log_mel(wave))[0]
        logits = sess.run(None, {sess.get_inputs()[0].name: x[None, None]})[0][0]
        top = {labels[k] for k in np.argsort(-logits)[:5]}
        self.assertTrue(top & {"Sine wave", "Beep, bleep", "Tuning fork"}, top)


if __name__ == "__main__":
    unittest.main()
