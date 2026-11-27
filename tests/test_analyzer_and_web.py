import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from clipfactory.config import Settings
from clipfactory.db import DB
from clipfactory.detect.analyzer import Candidate, ClaudeAnalyzer, MomentAnalysis
from clipfactory.detect.signals import Word


class FakeMessages:
    def __init__(self, parsed, stop="end_turn"):
        self.parsed, self.stop, self.kwargs = parsed, stop, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(parsed_output=self.parsed, stop_reason=self.stop, model=kwargs["model"],
                               usage=SimpleNamespace(input_tokens=3000, output_tokens=500))


def _analyzer(parsed, stop="end_turn"):
    a = ClaudeAnalyzer.__new__(ClaudeAnalyzer)
    import anthropic
    a.anthropic, a.model, a.effort, a.max_calls, a.calls, a.costs = anthropic, "claude-opus-5-5", "low", 30, [], []
    a.cost_hook = lambda usd, detail: a.costs.append(usd)
    a.client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(parsed, stop)))
    return a


CAND = Candidate("zerator", "ZeratoR", 160, 100, 185, 20, {"loud": 8, "chat": 12, "speech": 9}, "rire",
                 ["c'est pas possible"], 2, [Word("Regardez", 140, 140.4), Word("ça.", 140.5, 141)],
                 [(158, "MDRRR"), (159, "MDRRR"), (159.5, "KEKW")])


def test_claude_analyzer_maps_structured_output():
    parsed = MomentAnalysis(interessant=True, pourquoi="Saut raté", categorie="fail", autonome=True, debut=30, fin=70,
                            potentiel_viral=82, accroche="Il saute depuis la tour…", titre="Le saut de l'année",
                            description="ZeratoR tente l'impossible.", hashtags=["#zerator", "fail"], risques=[])
    a = _analyzer(parsed)
    res = a.analyze(CAND)
    assert (res.start, res.end, res.content_score, res.category) == (130, 170, 0.82, "fail")
    assert res.hashtags == ["zerator", "fail"]
    assert abs(a.costs[0] - (3000 * 4 + 500 * 20) / 1e6) < 1e-9
    kw = a.client.beta.messages.kwargs
    assert kw["fallbacks"] == "default" and kw["output_format"] is MomentAnalysis
    assert "MDRRR" in kw["messages"][0]["content"] and "×2" in kw["messages"][0]["content"]


def test_claude_analyzer_refusal_falls_back():
    assert _analyzer(None, "refusal").analyze(CAND) is None


def test_claude_budget_guard():
    a = _analyzer(None)
    a.max_calls, a.calls = 1, [time.time()]
    assert a.analyze(CAND) is None and a.client.beta.messages.kwargs is None


def test_dashboard_api(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    from clipfactory.web.app import create_app

    db = DB(tmp_path / "d.db")
    s = Settings({"data_dir": str(tmp_path)})
    cid = db.insert("clips", {"login": "zerator", "status": "en_attente", "score": 80, "title": "T", "created_at": time.time(),
                              "signals": {"reaction": "rire"}, "analysis": {"engine": "heuristique", "risks": []}})
    client = TestClient(create_app(db, s))
    assert client.get("/").status_code == 200
    assert client.get("/api/overview").json()["clip_counts"] == {"en_attente": 1}
    assert client.get("/api/clips?status=en_attente").json()[0]["id"] == cid
    r = client.post(f"/api/clips/{cid}/edit", json={"hook": "  Nouvelle accroche "})
    assert r.json()["hook"] == "Nouvelle accroche"
    assert client.post(f"/api/clips/{cid}/approve", json={}).status_code == 409  # pas de vidéo / pas d'autorisation
    assert client.post(f"/api/clips/{cid}/reject").json() == {"ok": True}


def test_dashboard_password(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    from clipfactory.web.app import create_app

    client = TestClient(create_app(DB(tmp_path / "d.db"), Settings({"data_dir": str(tmp_path)})))
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", auth=("admin", "s3cret")).status_code == 200
