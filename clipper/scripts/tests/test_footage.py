"""Footage: the working picture, its crop box and the prefilter. No ffmpeg needed.
Run all tests:  python -m unittest discover -s clipper/scripts/tests   (from the project folder,
with the private env's python: ~/.clipper/venv/Scripts/python.exe)"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper.footage import BASE, Footage  # noqa: E402


def sample(fps=60.0):
    return Footage(1920, 1080, fps, 31.0)


class WorkingPicture(unittest.TestCase):
    def test_uncropped_size_is_the_files(self):
        f = sample()
        self.assertEqual((f.width, f.height), (1920, 1080))

    def test_cropped_size_is_the_boxs(self):
        f = sample().cropped((160, 90, 1600, 900))
        self.assertEqual((f.width, f.height), (1600, 900))
        self.assertEqual((f.source_width, f.source_height), (1920, 1080))

    def test_odd_values_round_down_to_even(self):
        self.assertEqual(sample().cropped((161, 91, 1599, 899)).crop, (160, 90, 1598, 898))

    def test_box_outside_the_picture_is_refused(self):
        for box in [(-2, 0, 800, 600), (0, 0, 2000, 600), (1200, 0, 800, 600), (0, 700, 800, 600)]:
            with self.assertRaises(ValueError, msg=box):
                sample().cropped(box)

    def test_tiny_box_is_refused(self):
        with self.assertRaises(ValueError):
            sample().cropped((0, 0, 32, 600))

    def test_from_probe_defaults(self):
        f = Footage.from_probe({"width": 1280, "height": 720, "duration": 5, "fps": 0})
        self.assertEqual((f.width, f.height, f.fps, f.duration), (1280, 720, 30.0, 5.0))


class Prefilter(unittest.TestCase):
    def test_nothing_to_do_is_a_null_filter(self):
        self.assertEqual(sample(30.0).prefilter(30.0), "[0:v]null" + BASE)

    def test_fps_cap_only(self):
        self.assertEqual(sample(60.0).prefilter(30.0), "[0:v]fps=30.000" + BASE)

    def test_crop_only(self):
        f = sample(30.0).cropped((160, 90, 1600, 900))
        self.assertEqual(f.prefilter(30.0), "[0:v]crop=1600:900:160:90" + BASE)

    def test_crop_then_fps(self):
        f = sample(60.0).cropped((160, 90, 1600, 900))
        self.assertEqual(f.prefilter(30.0), "[0:v]crop=1600:900:160:90,fps=30.000" + BASE)

    def test_half_a_frame_of_difference_does_not_drop_frames(self):
        self.assertEqual(sample(30.4).prefilter(30.0), "[0:v]null" + BASE)


if __name__ == "__main__":
    unittest.main()
