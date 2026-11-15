"""Layout filtergraphs against what the code produced before Footage and Framing existed
(golden_layout.json, recorded from the original code on the synthetic inputs in cases.py).
The only intended difference: graphs start at [base] instead of [0:v]."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import cases  # noqa: E402
from clipper import config, layout  # noqa: E402
from clipper.footage import Footage  # noqa: E402


def _decide(cfg, footage, analysis, duration):
    framing = layout.decide(None if analysis is None else layout.Analysis(*analysis),
                            duration, footage, cfg)
    return framing.graph, framing.look, framing.cam_h


API = SimpleNamespace(
    make_info=lambda w, h, fps: Footage(w, h, fps, cases.DURATION),
    face=layout.Face, fit=layout.fit, stream=layout._stream, follow=layout._follow,
    decide=_decide,
)


class LayoutGraphs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = json.loads((HERE / "golden_layout.json").read_text(encoding="utf-8"))
        cls.new = cases.run_all(API, config.load())

    def test_same_cases(self):
        self.assertEqual(set(self.old), set(self.new))

    def test_every_graph_matches_the_old_one(self):
        for name, old in self.old.items():
            with self.subTest(name):
                self.assertEqual(self.new[name], old.replace("[0:v]", "[base]"))

    def test_no_graph_reads_the_file_directly(self):
        for name, graph in self.new.items():
            with self.subTest(name):
                self.assertNotIn("[0:v]", graph)

    def test_the_scene_change_case_really_splits(self):
        self.assertEqual(self.new["graph_two_parts_look"], "stream+face|None")
        self.assertIn("[in_0]", self.new["graph_two_parts"])
        self.assertIn("[in_1]", self.new["graph_two_parts"])


if __name__ == "__main__":
    unittest.main()
