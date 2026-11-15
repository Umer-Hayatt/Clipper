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

    def test_talking_all_the_way_means_no_place(self):
        w = self._talk((0.5, 3.5))
        self.assertIsNone(sfx.in_pause(1.2, w, self.RATE))

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
        self.assertEqual((d["on_beat"], d["per_clip"], d["no_pause"]), ("auto", 1, "skip"))
        self.assertIsNone(d["on_hook"])
        self.assertFalse(config.DEFAULTS["music"]["enabled"])


class TestLibrary(unittest.TestCase):
    def _lib(self, tmp):
        import json
        lib = Path(tmp)
        sounds = {"camera": ("sfx", ["stinger", "funny"]), "horn": ("sfx", ["stinger", "rage"]),
                  "riser": ("sfx", ["transition"]), "bed": ("music", ["tense"])}
        for name, (kind, tags) in sounds.items():
            (lib / kind).mkdir(exist_ok=True)
            (lib / kind / f"{name}.wav").write_bytes(b"RIFF")
        (lib / "manifest.json").write_text(json.dumps({"sounds": {
            n: {"kind": k, "tags": t, "confirmed": n == "camera"} for n, (k, t) in sounds.items()}}))
        return lib

    def test_mood_picks_the_matching_stinger(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = {"library": str(self._lib(tmp))}
            self.assertEqual(sfx.choose("sfx", "rage", "clip1", part, "stinger").stem, "horn")
            self.assertEqual(sfx.choose("sfx", "funny", "clip1", part, "stinger").stem, "camera")
            # no match: any stinger, never the non-stinger "riser"
            picks = {sfx.choose("sfx", "story", f"c{i}", part, "stinger").stem for i in range(20)}
            self.assertEqual(picks, {"camera", "horn"})

    def test_named_sound_and_confirmed_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = str(self._lib(tmp))
            self.assertEqual(sfx.choose("sfx", None, "c", {"library": lib, "sound": "riser"}).stem,
                             "riser")
            self.assertIsNone(sfx.choose("sfx", None, "c", {"library": lib, "sound": "nope"}))
            only = {"library": lib, "confirmed_only": True}
            self.assertEqual(sfx.choose("sfx", "rage", "c", only, "stinger").stem, "camera")

    def test_empty_library_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(sfx.choose("music", "tense", "c", {"library": tmp}))

    def test_music_bed_is_looped_ducked_and_faded(self):
        with tempfile.TemporaryDirectory() as tmp:
            bed = Path(tmp) / "bed.wav"
            bed.write_bytes(b"RIFF")
            inputs, graph = sfx.mix([], 2, Path(tmp), 0.5,
                                    music={"path": str(bed), "dur": 30, "volume": 0.3,
                                           "fade_out": 20})
        self.assertEqual(inputs[:3], ["-stream_loop", "-1", "-i"])
        self.assertIn("sidechaincompress", graph)
        self.assertIn("afade=t=out:st=19.200", graph)
        self.assertIn("[sxmain][musd]amix=inputs=2", graph)

    def test_dropped_hit_is_not_mixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sfx.mix([(None, "thud", "beat")], 1, Path(tmp), 0.5),
                             ([], "[a0]anull[a]"))


class TestStingerPlacement(unittest.TestCase):
    """render._place_hits: one stinger, on the latest beat that has a real pause after it."""

    def _run(self, sounds, **sfx_cfg):
        from clipper import render
        rate = 16000
        rng = np.random.default_rng(1)
        w = (rng.standard_normal(rate * 10) * 0.002).astype(np.float32)
        for a, b in [(0.5, 3.0), (3.6, 10.0)]:         # one real pause: 3.0-3.6
            w[int(a * rate):int(b * rate)] = rng.standard_normal(int((b - a) * rate)) * 0.3
        cfg = {"sfx": dict({"per_clip": 1, "pause_reach": 1.5, "no_pause": "skip"}, **sfx_cfg)}
        old = render.pcm
        render.pcm = lambda *a, **k: w
        try:
            return render._place_hits(sounds, cfg, "x", 0.0, 10.0)
        finally:
            render.pcm = old

    def test_last_beat_without_pause_falls_back_to_an_earlier_one(self):
        out = self._run([(2.5, "horn", "beat"), (8.0, "horn", "beat")])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0][0], 3.0, delta=0.05)

    def test_no_beat_with_a_pause_means_no_stinger(self):
        self.assertEqual(self._run([(5.0, "horn", "beat"), (8.0, "horn", "beat")]), [])

    def test_keep_puts_it_on_the_last_beat(self):
        self.assertEqual(self._run([(5.0, "horn", "beat"), (8.0, "horn", "beat")],
                                   no_pause="keep"), [(8.0, "horn", "beat")])

    def test_other_sounds_pass_through(self):
        self.assertEqual(self._run([(0.0, "whoosh", "hook")], per_clip=0),
                         [(0.0, "whoosh", "hook")])


class TestSoundLibraryCuts(unittest.TestCase):
    def test_trim_finds_the_hit_and_levels_it(self):
        import sound_library as sl
        sr = sl.SR
        x = np.zeros((sr * 2, 2), np.float32) + 0.001
        t = np.arange(int(0.3 * sr)) / sr
        hit = (np.sin(2 * np.pi * 300 * t) * np.exp(-t / 0.05)).astype(np.float32)
        x[int(0.5 * sr):int(0.8 * sr)] += hit[:, None] * 0.5
        cut, off = sl.trim_hit(x, look_before=0.3)
        self.assertAlmostEqual(off, 0.48, delta=0.03)
        self.assertLess(len(cut) / sr, 0.6)
        out = sl.finish(cut, "sfx")
        self.assertAlmostEqual(float(np.abs(out).max()), 0.891, delta=0.01)


if __name__ == "__main__":
    unittest.main()
