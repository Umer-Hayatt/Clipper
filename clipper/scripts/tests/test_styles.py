"""Caption looks, presets, keywords, beats and sound-effect wiring (no rendering)."""

import copy
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper import captions, config, moments, sfx  # noqa: E402


def _words(text, start=0.0, step=0.4):
    return [{"s": round(start + i * step, 2), "e": round(start + i * step + 0.3, 2), "w": w}
            for i, w in enumerate(text.split())]


class TestCaptionText(unittest.TestCase):
    def test_word_chunk_shows_one_word_at_a_time(self):
        cap = dict(config.DEFAULTS["captions"], chunk="word", max_words=1)
        toks = captions._tokens(_words("we are so back right now"), cap)
        groups = captions._groups(toks, lambda g: True, cap)
        self.assertEqual([len(g) for g in groups], [1] * 6)

    def test_phrase_chunk_unchanged(self):
        cap = config.DEFAULTS["captions"]
        toks = captions._tokens(_words("we are so back right now"), cap)
        groups = captions._groups(toks, lambda g: True, cap)
        self.assertEqual(sum(len(g) for g in groups), 6)
        self.assertGreater(min(len(g) for g in groups), 1)

    def test_profanity_masked_only_when_asked(self):
        cap = dict(config.DEFAULTS["captions"], case="upper")
        self.assertEqual(captions._tokens(_words("what the fuck"), cap)[2]["w"], "FUCK")
        cap["mask_profanity"] = True
        self.assertEqual(captions._tokens(_words("what the fuck?"), cap)[2]["w"], "F***?")
        self.assertEqual(captions._tokens(_words("classic"), cap)[0]["w"], "CLASSIC")

    def test_keywords_match_whole_phrases(self):
        toks = captions._tokens(_words("I love red dead redemption and red apples"),
                                config.DEFAULTS["captions"])
        captions.mark_keywords(toks, ["Red Dead Redemption"])
        self.assertEqual([t["w"] for t in toks if t.get("key")], ["red", "dead", "redemption"])


class TestPresets(unittest.TestCase):
    def test_mix_is_stable_per_clip_and_user_keys_win(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["caption_preset"] = "mix"
        cfg["_user"] = {"captions": {"outline": 9}}
        a, name_a = config.for_clip(cfg, 123.4)
        b, name_b = config.for_clip(cfg, 123.4)
        self.assertEqual(name_a, name_b)
        self.assertIn(name_a, config.PRESETS)
        self.assertEqual(a["captions"]["outline"], 9)
        self.assertEqual(cfg["captions"]["outline"], 3)       # the run's cfg is not changed

    def test_mix_spreads_over_presets(self):
        cfg = dict(copy.deepcopy(config.DEFAULTS), caption_preset="mix")
        names = {config.for_clip(cfg, t)[1] for t in range(0, 4000, 37)}
        self.assertGreaterEqual(len(names), 4)

    def test_default_needs_nothing_extra(self):
        self.assertEqual(config.text_needs(copy.deepcopy(config.DEFAULTS)), set())

    def test_keyword_look_and_beats_need_fields(self):
        cfg = dict(copy.deepcopy(config.DEFAULTS), caption_preset="mix",
                   mix_presets=["keyword"])
        cfg["beats"] = dict(cfg["beats"], enabled=True)
        self.assertEqual(config.text_needs(cfg), {"keywords", "beats"})


class TestBeats(unittest.TestCase):
    def test_beat_snaps_to_spoken_word_and_is_clip_time(self):
        words = _words("oh my god no way that happened", start=100.0, step=0.5)
        m = {"start": 98.0, "end": 130.0}
        got = moments._beats([{"at": 101.0, "text": "NO WAY!"}, {"at": 500, "text": "x"}],
                             m, words, config.DEFAULTS)
        self.assertEqual(got, [{"at": 3.5, "text": "NO WAY!"}])

    def test_zoom_off_without_beats(self):
        self.assertIsNone(captions.zoom_filter([], config.DEFAULTS, None, "a", "b"))


class TestSfx(unittest.TestCase):
    def test_mix_wires_inputs_and_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs, graph = sfx.mix([(0.0, "whoosh"), (2.5, "boom"), (1.0, "nope")], 3,
                                    Path(tmp), 0.5)
            self.assertEqual(inputs.count("-i"), 2)
            self.assertTrue((Path(tmp) / "sfx" / f"boom_v{sfx.VERSION}.wav").exists())
        self.assertIn("adelay=2500:all=1", graph)
        self.assertIn("amix=inputs=3", graph)
        self.assertTrue(graph.endswith("[a]"))

    def test_no_events_passes_audio_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sfx.mix([], 1, Path(tmp), 0.5), ([], "[a0]anull[a]"))

    def test_every_sound_reaches_phone_speakers(self):
        """At least 30% of each sound's energy is above 150 Hz (phones play little below;
        the old boom had 1%)."""
        for name in sfx.NAMES:
            x = sfx._MAKERS[name]()
            spec = np.abs(np.fft.rfft(x)) ** 2
            freqs = np.fft.rfftfreq(len(x), 1 / sfx.SR)
            self.assertGreater(spec[freqs >= 150].sum() / spec.sum(), 0.30, name)


class TestHitInPause(unittest.TestCase):
    RATE = 16000

    def _talk(self, *spans, total=4.0):
        """Loud noise ("talking") in the given (start, end) spans, quiet hiss elsewhere."""
        rng = np.random.default_rng(3)
        w = rng.standard_normal(int(total * self.RATE)) * 0.002
        for a, b in spans:
            i, j = int(a * self.RATE), int(b * self.RATE)
            w[i:j] = rng.standard_normal(j - i) * 0.3
        return w.astype(np.float32)

    def test_waits_for_the_first_real_pause(self):
        # a 60 ms dip at 1.40 is too short; the real pause starts at 1.90
        w = self._talk((0.5, 1.4), (1.46, 1.9), (2.4, 3.5))
        self.assertAlmostEqual(sfx.in_pause(1.2, w, self.RATE), 1.9, delta=0.03)

    def test_already_in_a_pause_stays(self):
        w = self._talk((0.5, 1.4), (2.4, 3.5))
        self.assertAlmostEqual(sfx.in_pause(1.8, w, self.RATE), 1.8, delta=0.03)

    def test_no_pause_within_reach_stays(self):
        w = self._talk((0.5, 3.5))
        self.assertEqual(sfx.in_pause(1.2, w, self.RATE), 1.2)

    def test_reach_zero_or_no_audio_stays(self):
        w = self._talk((0.5, 1.4))
        self.assertEqual(sfx.in_pause(1.2, w, self.RATE, reach=0), 1.2)
        self.assertEqual(sfx.in_pause(1.2, None, self.RATE), 1.2)
        self.assertEqual(sfx.in_pause(9.0, w, self.RATE), 9.0)    # past the end

    def test_mix_accepts_tagged_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs, _ = sfx.mix([(1.0, "thud", "beat")], 1, Path(tmp), 0.5)
        self.assertEqual(inputs.count("-i"), 1)

    def test_defaults_match_the_references(self):
        d = config.DEFAULTS["sfx"]
        self.assertFalse(d["enabled"])                         # still opt-in
        self.assertEqual(d["on_beat"], "thud")
        self.assertIsNone(d["on_hook"])


if __name__ == "__main__":
    unittest.main()
