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
