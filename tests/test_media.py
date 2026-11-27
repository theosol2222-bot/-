import numpy as np

from clipfactory.detect.signals import Word
from clipfactory.media.captions import CaptionStyle, build_ass, esc, group_words, wrap_hook
from clipfactory.media.render import Layout, keep_segments, layout_graph, remap, remap_words, tracking_expr


def W(text, s, e):
    return Word(text, s, e)


def test_keep_segments_removes_long_silences_only():
    words = [W("a", 10.5, 11), W("b", 11.1, 11.5), W("c", 15, 15.5), W("d", 15.6, 16)]
    segs = keep_segments(10, 17, words, db=np.full(7, -30.0), min_gap=1.2)
    assert segs == [(10, 11.75), (14.75, 17)]


def test_keep_segments_keeps_loud_gap():
    words = [W("a", 10.5, 11), W("c", 15, 15.5)]
    db = np.full(7, -30.0)
    db[2] = -5  # un cri sans paroles entre les deux mots
    assert keep_segments(10, 17, words, db=db) == [(10, 17)]


def test_keep_segments_trims_silent_intro_for_hook():
    words = [W("a", 14, 14.5), W("b", 14.6, 15)]
    segs = keep_segments(10, 16, words, db=np.full(6, -30.0))
    assert segs[0][0] == 13.75


def test_remap_and_words():
    segs = [(10, 12), (14, 16)]
    assert remap(11, segs) == 1 and remap(15, segs) == 3 and remap(13, segs) is None
    out = remap_words([W("x", 15, 15.5)], segs)
    assert out[0].start == 3 and out[0].end == 3.5


def test_group_words_breaks_on_punctuation_and_length():
    ws = [W(t, i * 0.3, i * 0.3 + 0.25) for i, t in enumerate("Non non non. C'est pas possible vous avez vu".split())]
    groups = [" ".join(w.text for w in g) for g in group_words(ws, 3, 20)]
    assert groups[0] == "Non non non." and all(len(g.split()) <= 3 for g in groups)


def test_ass_escaping_and_hook_wrap():
    assert esc("a{b}\\c 😂") == "a(b)c"
    assert "\\N" in wrap_hook("Il ne s'attendait pas du tout à ça", 18)
    ass = build_ass([W("salut", 0.2, 0.6), W("{test}", 0.7, 1.0)], 5, CaptionStyle(), hook="Regardez 😂", credit="twitch.tv/x")
    assert "Dialogue" in ass and "{test}" not in ass and "😂" not in ass and "twitch.tv/x" in ass


def test_layout_graphs_are_well_formed():
    for lay in (Layout("fill"), Layout("center", focus=0.3), Layout("split", cam=(0, 400, 400, 300)),
                Layout("center", track=[(0, 0.4), (2, 0.5), (4, 0.6)])):
        g = layout_graph(lay, 1920, 1080)
        assert g.startswith("[vc]") and g.endswith("[lv]")
        assert g.count("(") == g.count(")")
    assert tracking_expr([(0, 100), (2, 200)]).startswith("if(lt(t,0.00)")


def test_tripod_camera_ignores_lone_jump_and_follows_confirmed_move():
    from clipfactory.media.face import TripodCamera

    cam = TripodCamera(crop_width=600, video_width=1920)
    cam.observe(900)                      # première détection : cadrage net
    assert cam.step(0.25) == 900
    cam.observe(1500)                     # saut isolé (faux positif) : ignoré
    cam.observe(905)
    assert cam.target == 905
    for _ in range(3):                    # vrai déplacement confirmé 3 fois
        cam.observe(1500)
    assert cam.target == 1500
    assert 900 < cam.step(0.25) < 1500    # panoramique progressif, pas de saut


def test_tripod_camera_snaps_on_scene_cut():
    from clipfactory.media.face import TripodCamera

    cam = TripodCamera(600, 1920)
    cam.observe(500)
    cam.cut()
    cam.observe(1400)
    assert cam.step(0.25) == 1400


def test_simplify_keeps_corners_only():
    from clipfactory.media.face import simplify

    pts = [(t / 4, 500.0) for t in range(20)] + [(5 + t / 4, 500 + t * 20.0) for t in range(10)]
    out = simplify(pts)
    assert len(out) <= 4 and out[0] == pts[0] and out[-1] == pts[-1]


def test_yunet_detects_public_domain_face():
    import cv2
    import pytest

    skimage = pytest.importorskip("skimage")
    from clipfactory.media.face import YUNET, detect_faces

    assert YUNET.exists()
    img = cv2.cvtColor(skimage.data.astronaut(), cv2.COLOR_RGB2BGR)
    faces = detect_faces(img)
    assert faces and 150 < faces[0][0] < 220


def test_keep_segments_respects_min_duration_after_silence_cut():
    from clipfactory.detect.signals import Word
    from clipfactory.media.render import keep_segments

    # 4 phrases d'1 s séparées de 6 s de silence : sans plancher il ne resterait que ~6 s
    words = [Word(f"mot{i}", 10 + i * 7, 11 + i * 7) for i in range(4)]
    cut = keep_segments(10, 32, words)
    assert sum(b - a for a, b in cut) < 10
    kept = keep_segments(10, 32, words, min_total=20)
    assert sum(b - a for a, b in kept) >= 20
    assert kept[0][0] >= 10 and kept[-1][1] <= 32
