"""Compréhension du moment : pourquoi est-il intéressant, quel contexte garder,
quel score viral. Deux moteurs :

- `ClaudeAnalyzer` : Claude lit la transcription horodatée + un résumé du chat
  et renvoie une analyse structurée (sortie validée par Pydantic) ;
- `heuristic_analysis` : repli gratuit et déterministe si pas de clé API,
  budget atteint ou refus.
"""
from __future__ import annotations

import logging
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from .signals import Word

log = logging.getLogger(__name__)

CATEGORIES = ("drole", "choc", "exploit", "fail", "rage", "emotion", "clash", "interaction", "insolite", "malaise", "autre")
PRICES_USD_PER_MTOK = {  # entrée, sortie
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


@dataclass
class Candidate:
    """Un pic à analyser, avec ses données brutes (temps absolus dans l'enregistrement)."""
    login: str
    streamer: str
    peak: float
    window_start: float
    window_end: float
    signal: float
    signals: dict[str, float]
    reaction: str
    phrases: list[str]
    laughs: int
    words: list[Word]
    chat_lines: list[tuple[float, str]]
    game: str = ""
    stream_title: str = ""
    mentions: list[str] = field(default_factory=list)


class MomentAnalysis(BaseModel):
    interessant: bool
    pourquoi: str
    categorie: Literal["drole", "choc", "exploit", "fail", "rage", "emotion", "clash", "interaction", "insolite", "malaise", "autre"]
    autonome: bool
    debut: float
    fin: float
    potentiel_viral: int
    accroche: str
    titre: str
    description: str
    hashtags: list[str]
    risques: list[str]


@dataclass
class Analysis:
    interesting: bool
    why: str
    category: str
    standalone: bool
    start: float
    end: float
    content_score: float          # 0..1
    hook: str
    title: str
    description: str
    hashtags: list[str]
    risks: list[str]
    engine: str
    viral_score: int = 0


# ---------------------------------------------------------------------------
def _transcript_block(c: Candidate) -> str:
    lines, current, t0 = [], [], None
    for w in c.words:
        if t0 is None:
            t0 = w.start
        current.append(w.text)
        if w.text.endswith((".", "!", "?", "…")) or len(current) >= 18:
            lines.append(f"[{t0 - c.window_start:6.1f}s] {' '.join(current)}")
            current, t0 = [], None
    if current:
        lines.append(f"[{t0 - c.window_start:6.1f}s] {' '.join(current)}")
    return "\n".join(lines) or "(aucune parole détectée)"


def _chat_block(c: Candidate, limit: int = 60) -> str:
    """Résumé du chat par tranches de 5 s : volume + messages les plus fréquents (sans pseudos)."""
    buckets: dict[int, Counter[str]] = {}
    for t, text in c.chat_lines:
        b = int((t - c.window_start) // 5) * 5
        buckets.setdefault(b, Counter())[text.strip()[:80]] += 1
    out = []
    for b in sorted(buckets):
        top = ", ".join(f"« {m} »" + (f" ×{n}" if n > 1 else "") for m, n in buckets[b].most_common(4))
        out.append(f"[{b:4d}s] {sum(buckets[b].values())} msg : {top}")
    return "\n".join(out[:limit]) or "(chat indisponible)"


SYSTEM = """Tu es éditeur·rice de clips TikTok spécialisé·e dans les streams Twitch francophones.
On te donne un extrait de live (transcription horodatée du streamer + résumé du chat) autour
d'un pic d'activité détecté automatiquement. Ton travail :
1. Comprendre POURQUOI ce moment fait réagir (blague, exploit, fail, rage, émotion, clash,
   interaction entre streamers, situation insolite…) — ou dire qu'il n'y a rien (faux positif :
   pub, musique, chat qui spamme sans raison, écran d'attente).
2. Choisir `debut` et `fin` (en secondes depuis le début de l'extrait) pour que la vidéo soit
   compréhensible SANS avoir vu le live : inclure la mise en place nécessaire et la chute,
   couper avant que ça retombe. Durée entre 20 et 90 s, idéalement 25-50 s.
3. Noter `potentiel_viral` de 0 à 100 avec exigence (50 = correct, 75 = très bon, 90+ = rare).
   Un moment drôle ou choquant compréhensible seul vaut plus qu'un pic de chat sans contexte.
4. Écrire en français naturel, style TikTok FR, sans emoji excessif ni clickbait mensonger :
   `accroche` (texte affiché les 3 premières secondes, ≤ 60 caractères), `titre` (≤ 90 car.),
   `description` (1-2 phrases + crédit du streamer), 4 à 7 `hashtags` sans « # ».
5. Lister les `risques` : musique sous licence audible, données personnelles à l'écran,
   propos pouvant nuire à une personne, contenu sensible. Liste vide si rien.
Ne jamais inventer de paroles qui ne sont pas dans la transcription."""


class ClaudeAnalyzer:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "low", max_calls_per_hour: int = 30,
                 cost_hook=None):
        import anthropic  # import local : dépendance optionnelle

        self.client = anthropic.Anthropic()
        self.anthropic = anthropic
        self.model = model
        self.effort = effort
        self.max_calls = max_calls_per_hour
        self.calls: list[float] = []
        self.cost_hook = cost_hook

    def budget_ok(self) -> bool:
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 3600]
        return len(self.calls) < self.max_calls

    def analyze(self, c: Candidate) -> Analysis | None:
        if not self.budget_ok():
            log.info("Budget LLM horaire atteint : analyse heuristique")
            return None
        self.calls.append(time.time())
        user = (
            f"Streamer : {c.streamer} (twitch.tv/{c.login})\n"
            f"Titre du live : {c.stream_title or '?'} — Jeu/catégorie : {c.game or '?'}\n"
            f"Extrait : {c.window_end - c.window_start:.0f} s ; pic détecté à {c.peak - c.window_start:.1f} s\n"
            f"Signaux au pic : {c.signals} ; réaction dominante du chat : {c.reaction} ; "
            f"expressions repérées : {', '.join(c.phrases) or 'aucune'} ; rires du streamer : {c.laughs}\n"
            f"Autres streamers mentionnés : {', '.join(c.mentions) or 'aucun'}\n\n"
            f"## Transcription\n{_transcript_block(c)}\n\n## Chat\n{_chat_block(c)}"
        )
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": self.effort},
                system=SYSTEM,
                messages=[{"role": "user", "content": user}],
                output_format=MomentAnalysis,
            )
        except self.anthropic.RateLimitError:
            log.warning("Claude : limite de débit, repli heuristique")
            return None
        except self.anthropic.APIStatusError as exc:
            log.warning("Claude : erreur API %s, repli heuristique", exc.status_code)
            return None
        except self.anthropic.APIConnectionError:
            log.warning("Claude : connexion impossible, repli heuristique")
            return None
        usage = response.usage
        price_in, price_out = PRICES_USD_PER_MTOK.get(response.model, PRICES_USD_PER_MTOK.get(self.model, (4.0, 20.0)))
        cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6
        if self.cost_hook:
            self.cost_hook(cost, f"{response.model} in={usage.input_tokens} out={usage.output_tokens}")
        if response.stop_reason == "refusal" or response.parsed_output is None:
            log.info("Claude : pas d'analyse exploitable (%s)", response.stop_reason)
            return None
        r: MomentAnalysis = response.parsed_output
        span = c.window_end - c.window_start
        start = c.window_start + max(0.0, min(span, r.debut))
        end = c.window_start + max(0.0, min(span, r.fin))
        if end <= start:
            start, end = c.window_start, c.window_end
        return Analysis(
            interesting=r.interessant, why=r.pourquoi.strip(), category=r.categorie, standalone=r.autonome,
            start=start, end=end, content_score=max(0, min(100, r.potentiel_viral)) / 100,
            hook=r.accroche.strip()[:70], title=r.titre.strip()[:100], description=r.description.strip(),
            hashtags=[h.strip().lstrip("#").replace(" ", "") for h in r.hashtags if h.strip()][:8],
            risks=r.risques, engine=response.model,
        )


# ---------------------------------------------------------------------------
HOOKS = {
    "rire": ["Il ne s'attendait pas à ça 😂", "Le chat est mort de rire", "J'en peux plus 😂"],
    "choc": ["Personne n'y croyait…", "Le chat n'en revient pas", "Regardez bien la fin"],
    "hype": ["Ce moment est INCROYABLE", "Le chat devient fou", "Quelle masterclass"],
    "malaise": ["Le malaise était total", "Gros moment de gêne", "Il aurait pas dû dire ça"],
    "intense": ["Ce moment en live…", "Ça a dégénéré", "Regardez sa réaction"],
}
CATEGORY_OF = {"rire": "drole", "choc": "choc", "hype": "exploit", "malaise": "malaise", "intense": "autre"}


def heuristic_analysis(c: Candidate, before: float = 22, after: float = 16) -> Analysis:
    """Analyse gratuite : règles sur les signaux, sans compréhension sémantique fine."""
    from .signals import snap_window

    start, end = snap_window(c.peak, c.words, before, after, c.window_start, c.window_end)
    words_in = [w for w in c.words if start <= w.start <= end]
    content = 0.35
    strong = [p for p in c.phrases if p not in ("attends", "quoi", "comment", "allez", "chaud")]
    content += min(0.2, 0.07 * len(strong))
    content += min(0.15, 0.05 * c.laughs)
    content += 0.1 if c.mentions else 0
    content -= 0.15 if len(words_in) < 15 else 0  # peu de parole : contexte difficile à saisir
    content = max(0.0, min(1.0, content))
    reaction = c.reaction if c.reaction in HOOKS else "intense"
    hook = HOOKS[reaction][int(c.peak) % len(HOOKS[reaction])]
    quote = " ".join(w.text for w in words_in)[:160]
    category = "interaction" if c.mentions else CATEGORY_OF.get(reaction, "autre")
    return Analysis(
        interesting=c.signals.get("chat", 0) >= 6 or len(strong) >= 2 or c.laughs >= 2,
        why=f"Pic {reaction} (signal {c.signal:.1f}) ; expressions : {', '.join(c.phrases[:4]) or '—'}"
            + (f" ; mention de {', '.join(c.mentions)}" if c.mentions else ""),
        category=category, standalone=len(words_in) >= 15, start=start, end=end,
        content_score=content, hook=hook,
        title=f"{c.streamer} : {quote[:70] + '…' if len(quote) > 70 else quote or hook}",
        description=f"Moment fort du live de {c.streamer}.",
        hashtags=[], risks=[], engine="heuristique",
    )


def viral_score(c: Candidate, a: Analysis, min_duration: float = 20, max_duration: float = 90) -> int:
    """Score de potentiel viral /100.

    35-45 % compréhension du contenu (Claude ou heuristique), le reste = intensité
    mesurée : pic combiné, emballement du chat, réaction vocale du streamer.
    """
    sat = lambda x, k: 1 - math.exp(-max(0.0, x) / k)  # noqa: E731
    # Le volume sature vite (toute phrase après un silence est « forte ») : poids faible.
    s_signal = sat(c.signal, 15)
    s_chat = sat(c.signals.get("chat", 0), 6)
    s_voice = sat(c.signals.get("speech", 0) + c.signals.get("loud", 0) * 0.25, 6)
    w_content = 0.45 if a.engine != "heuristique" else 0.35
    rest = 1 - w_content
    score = 100 * (w_content * a.content_score + rest * (0.4 * s_signal + 0.35 * s_chat + 0.25 * s_voice))
    duration = a.end - a.start
    if duration < min_duration * 0.75 or duration > max_duration * 1.1:
        score -= 8
    if not a.standalone:
        score -= 10
    if not a.interesting:
        score = min(score, 35)
    if c.mentions:
        score += 4
    return int(max(0, min(100, round(score))))
