"""Synthetic inputs for the layout filtergraph tests: no video, no ffmpeg, no face model.
`run_all` builds every case through an `api` object, so the same inputs can be run against
an older copy of the code to record what it produced (see golden_layout.json).

`api` offers: make_info(w, h, fps), face(t, x, y, w, h), fit(cfg, info), stream(cfg, info,
cam, face=None), follow(cfg, info, faces, grays, scale) and decide(cfg, info, analysis,
duration) -> (graph, look, cam_h). `analysis` is (faces, times, grays, scale) or None."""

import numpy as np

SIZE = (1920, 1080)
FPS = 60.0
DURATION = 31.0


def grays(n, seed, black_top=0, black_bottom=0):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        g = rng.integers(40, 200, (270, 480), dtype=np.uint8)
        if black_top:
            g[:black_top] = 0
        if black_bottom:
            g[-black_bottom:] = 0
        out.append(g)
    return out


def times(n):
    return [round(i * 0.5, 3) for i in range(n)]


def run_all(api, cfg):
    """{case name: filtergraph text}."""
    info = api.make_info(*SIZE, FPS)
    face_at = lambda ts, box: [api.face(t, *box) for t in ts]  # noqa: E731
    out = {}
    out["fit"] = api.fit(cfg, info)
    out["fit_no_aspect"] = api.fit(dict(cfg, fit_aspect=0), info)
    out["fit_ultrawide"] = api.fit(cfg, api.make_info(2560, 1080, FPS))

    out["stream_right"] = api.stream(cfg, info, (1400, 60, 400, 300), (1500, 100, 120, 150))
    out["stream_right_noface"] = api.stream(cfg, info, (1400, 60, 400, 300))
    out["stream_left"] = api.stream(cfg, info, (40, 60, 520, 300), (150, 90, 110, 140))

    t20 = times(20)
    big = face_at(t20, (800, 300, 300, 300))
    out["follow"] = api.follow(cfg, info, big, grays(4, 1), 0.25)
    out["follow_black_bars"] = api.follow(cfg, info, big, grays(4, 2, 14, 14), 0.25)

    out["graph_fit"] = api.decide(dict(cfg, layout="fit"), info, None, 10)[0]
    small = face_at(t20, (1600, 80, 100, 100))
    g = grays(20, 3)
    cases = {
        "graph_face": big,
        "graph_stream": small,
        "graph_two_parts": small[:10] + big[10:],
    }
    for name, faces in cases.items():
        got = api.decide(cfg, info, (faces, t20, g, 0.25), 10)
        out[name] = got[0]
        out[name + "_look"] = f"{got[1]}|{got[2]}"
    return out
