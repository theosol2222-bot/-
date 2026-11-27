import numpy as np

from clipfactory.detect import lexicon
from clipfactory.detect import signals as sig
from clipfactory.detect.analyzer import Analysis, Candidate, heuristic_analysis, viral_score


def test_normalize_keeps_french_words():
    # Le skill d'origine transformait « énorme » en « norme » et « là » en « l ».
    assert lexicon.normalize("C'est ÉNORME, oh là là !") == "c'est enorme oh la la"
    assert lexicon.normalize("T’es sérieux ?") == "t'es serieux"


def test_classify_real_french_chat():
    assert "rire" in lexicon.classify_chat("mdrrrrr")
    assert "rire" in lexicon.classify_chat("ptdrr")
    assert "rire" in lexicon.classify_chat("bshahahahaha")
    assert "rire" in lexicon.classify_chat("KEKW KEKW")
    assert "choc" in lexicon.classify_chat("WTF ???")
    assert "clip" in lexicon.classify_chat("CLIP ÇA")
    assert "hype" in lexicon.classify_chat("W")
    assert not lexicon.classify_chat("il est où le passage")
    assert not lexicon.classify_chat("@lhyx")


def test_channel_emotes_and_repeats():
    assert lexicon.emote_count("jdgFlou jdgFlou zeratoRIRE") == 3
    assert lexicon.emote_count("bonjour tout le monde") == 0
    assert lexicon.chat_key("MDRRRRRRR") == lexicon.chat_key("mdrrr")


def test_speech_signal_finds_french_phrases():
    words = [sig.Word(t, 10 + i * 0.3, 10.25 + i * 0.3) for i, t in
             enumerate("Non non non c'est pas possible vous avez vu ça ? Ahahaha clippez ça".split())]
    res = sig.speech_signal(words, 30)
    hits = {p for v in res.hits.values() for p in v}
    assert "c'est pas possible" in hits and "vous avez vu ca" in hits and "clippez" in hits
    assert res.laughs >= 1
    assert res.score[10:16].max() > 3


def test_chat_signal_spike_and_reaction():
    msgs = [(t, "salut") for t in range(0, 120, 3)] + [(66 + i * 0.1, m) for i, m in
                                                     enumerate(["MDRRR", "ptdr", "KEKW", "CLIP", "mdr"] * 12)]
    res = sig.chat_signal(msgs, 120, delay=6)
    peak = int(np.argmax(res.score))
    assert 58 <= peak <= 70
    assert sig.dominant_reaction(res, 55, 75) == "rire"
    assert res.repeats.sum() > 10


def test_pick_peaks_non_overlapping():
    score = np.zeros(300)
    score[50], score[60], score[200] = 9, 8, 7
    peaks = sig.pick_peaks(score, count=5, threshold=1, gap=30)
    assert [int(p.peak) for p in peaks] == [50, 200]


def test_local_z_robust_to_constant_loudness():
    flat = np.full(200, -20.0)
    flat[100] = -5
    z = sig.local_z(flat, 60, 3)
    assert z[100] > 4 and abs(z[10]) < 1e-9


def _cand(chat, speech, laughs=0, phrases=None, words=40):
    ws = [sig.Word("mot", 100 + i * 0.5, 100.4 + i * 0.5) for i in range(words)]
    return Candidate("zerator", "ZeratoR", 110, 50, 135, chat + speech, {"loud": 8, "chat": chat, "speech": speech},
                     "rire", phrases or [], laughs, ws, [])


def test_viral_score_separates_real_moment_from_noise():
    strong = _cand(14, 10, laughs=2, phrases=["c'est pas possible", "vous avez vu", "clippez"])
    weak = _cand(3.5, 2)
    s_strong = viral_score(strong, heuristic_analysis(strong))
    s_weak = viral_score(weak, heuristic_analysis(weak))
    assert s_strong >= 70 and s_weak <= 40


def test_viral_score_penalizes_non_standalone():
    c = _cand(10, 6, laughs=1, phrases=["c'est fou", "dingue"])
    a = heuristic_analysis(c)
    base = viral_score(c, a)
    a2 = Analysis(**{**a.__dict__, "standalone": False})
    assert viral_score(c, a2) == base - 10
