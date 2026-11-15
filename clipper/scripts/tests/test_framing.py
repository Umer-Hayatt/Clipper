"""Framing: layout.decide() is a pure function of what it is given. No ffmpeg, no video."""

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import cases  # noqa: E402
from clipper import config, layout  # noqa: E402
from clipper.footage import Footage  # noqa: E402

FOOTAGE = Footage(1920, 1080, 60.0, 31.0)
T = cases.times(20)
GRAYS = cases.grays(20, 3)
CAM = (1400, 60, 400, 300)                       # a facecam spot learned from another clip


def analysis(faces):
    return layout.Analysis(faces, T, GRAYS, 0.25)


def faces_at(*spots, size=100):
    return [layout.Face(t, x, y, size, size) for t in T for x, y in spots]


class Decide(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_a_small_cam_gives_the_stream_look_and_reports_the_cam(self):
        framing = layout.decide(analysis(faces_at((1600, 80))), 10, FOOTAGE, self.cfg)
        self.assertEqual(framing.look, "stream")
        self.assertEqual(framing.cam_h, 760)
        self.assertEqual(len(framing.cams), 1)
        self.assertIn("(found by border)", framing.notes[0])
        self.assertEqual(framing.notes[-1], "  look: stream")

    def test_a_big_face_gives_the_face_look_and_no_cam(self):
        big = [layout.Face(t, 800, 300, 300, 300) for t in T]
        framing = layout.decide(analysis(big), 10, FOOTAGE, self.cfg)
        self.assertEqual((framing.look, framing.cam_h, framing.cams), ("face", None, []))

    def test_layout_fit_needs_no_analysis(self):
        framing = layout.decide(None, 10, FOOTAGE, dict(self.cfg, layout="fit"))
        self.assertEqual((framing.look, framing.cam_h, framing.notes), ("fit", None, []))

    def test_it_is_pure(self):
        known = [CAM]
        before = copy.deepcopy(known)
        a = analysis(faces_at((1600, 80)))
        first = layout.decide(a, 10, FOOTAGE, self.cfg, known)
        second = layout.decide(a, 10, FOOTAGE, self.cfg, known)
        self.assertEqual(first, second)
        self.assertEqual(known, before)

    def test_scene_change_is_one_framing_with_both_looks(self):
        faces = faces_at((1600, 80))[:10] + [layout.Face(t, 800, 300, 300, 300) for t in T[10:]]
        framing = layout.decide(analysis(faces), 10, FOOTAGE, self.cfg)
        self.assertEqual(framing.look, "stream+face")
        self.assertTrue(any("scene changes inside the clip" in n for n in framing.notes))


class UnclearScenes(unittest.TestCase):
    """Three faces on screen all the time (thumbnails?): unclear, so what other clips of the
    stream taught decides."""

    def setUp(self):
        self.cfg = config.load()
        self.faces = faces_at((1500, 100), (200, 600), (900, 700))

    def test_without_a_known_cam_the_whole_frame_is_used(self):
        framing = layout.decide(analysis(self.faces), 10, FOOTAGE, self.cfg, [])
        self.assertEqual(framing.look, "fit")
        self.assertTrue(any("thumbnails" in n for n in framing.notes))

    def test_a_known_cam_with_a_face_in_it_is_used(self):
        framing = layout.decide(analysis(self.faces), 10, FOOTAGE, self.cfg, [CAM])
        self.assertEqual(framing.look, "stream")
        self.assertEqual(framing.cams, [])          # nothing new was found, only reused
        self.assertTrue(any("unclear scene" in n for n in framing.notes))


if __name__ == "__main__":
    unittest.main()
