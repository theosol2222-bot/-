"""Lexique francophone : réactions du streamer et du chat Twitch FR.

Calibré sur du chat FR réel (mdrrrrr, ptdrr, bshahahaha, xD, W, LUL, emotes de
chaîne type `jdgFlou`). Corrige le défaut du skill clip-stream d'origine dont la
normalisation `[^a-z0-9' ]` détruisait les accents (« énorme » → « norme »).
"""
from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    """Minuscules, sans accents, apostrophes unifiées, ponctuation retirée."""
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[’`´]", "'", text)
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --- Ce que dit le streamer (transcription) ---------------------------------
# poids : 4 = demande explicite de clip, 3 = choc fort, 2 = réaction, 1.2 = faible
STREAMER_PHRASES: dict[str, float] = {
    # demandes de clip (Whisper entend parfois « clic »)
    "clippez": 4, "clippe ca": 4, "clip ca": 4, "clic ca": 4, "faut clip": 4, "c'est clip": 4, "le clip": 3,
    # choc / incrédulité
    "c'est pas possible": 3, "c'est pas vrai": 3, "j'y crois pas": 3, "je reve": 3, "t'es serieux": 3,
    "vous avez vu": 3, "t'as vu": 2.5, "vous avez vu ca": 3.5, "il a fait quoi": 3, "qu'est ce qui se passe": 2.5,
    "mais qu'est ce que": 2.5, "c'est quoi ce": 2.5, "oh mon dieu": 3, "mon dieu": 2.5, "nan mais": 2,
    "attends attends": 2.5, "attends": 1.2, "quoi": 1.2, "comment": 1.2, "non non non": 3, "non mais": 2,
    "c'est fou": 3, "c'est dingue": 3, "c'est incroyable": 3, "incroyable": 2.5, "dingue": 2, "de ouf": 2.5,
    "chaud": 1.2, "trop chaud": 2.5, "masterclass": 3, "enorme": 2.5, "abuse": 2, "n'importe quoi": 2.5,
    "je suis choque": 3, "choque": 2, "ca va pas": 2, "c'est une dinguerie": 3, "dinguerie": 3, "la folie": 2.5,
    # hype / victoire
    "let's go": 2.5, "lets go": 2.5, "allez": 1.5, "oui oui oui": 3, "ouiii": 2.5, "top 1": 3, "on l'a fait": 3,
    "gg": 1.5, "c'est gagne": 3, "victoire": 2.5,
    # échec / rage / peur
    "c'est pas possible ca": 3, "j'ai perdu": 2, "on est morts": 2.5, "je suis mort": 2, "au secours": 3,
    "j'ai peur": 2.5, "j'ai eu peur": 3, "il m'a tue": 2, "putain": 2, "merde": 1.5, "oh non": 2.5, "noooon": 3,
    # gêne / drôle
    "je suis gene": 2.5, "malaise": 2.5, "c'est gênant": 2.5, "je meurs": 2.5, "je suis plie": 3, "mort de rire": 3,
}

STREAMER_LAUGH = re.compile(r"\b((?:a?h+a+){2,}h*|(?:h+i+){2,}|mdr+|ptdr+|\(?rires?\)?|\[?rires?\]?|lol)\b", re.I)

# --- Ce qu'écrit le chat -----------------------------------------------------
CHAT_LAUGH = re.compile(
    r"^(x?mdr+|x?ptdr+|jpp+|lol+|lmao+|mort|je meurs|(?:b?s?h*a+h+)+a*h*|(?:a?h+a+){2,}h*|(?:h+i+){2,}h*|xd+|"
    r"kekw|omegalul|lul|pepelaugh|icant|ptn mdr|😂+|🤣+|💀+)$", re.I)
CHAT_SHOCK = re.compile(
    r"^(wtf+|quoi+|hein+|\?{2,}|omg+|oh+|nan+|non+|choque|chokbar|dinguerie|pog+|poggers|pogchamp|monkas|"
    r"monkaw|ayaa+|wesh+|wsh+|fou+|ouf+|incroyable|😱+|😳+|🤯+|o+o+)$", re.I)
CHAT_HYPE = re.compile(r"^(w+|gg+|let'?s ?go+|lets?go+|go+|ez+|goat|masterclass|🔥+|🐐+|letsgo+|énorme|enorme|propre)$", re.I)
CHAT_CRINGE = re.compile(r"^(malaise|gênant|genant|cringe|aie+|aïe+|oula+|ouille+|😬+)$", re.I)
CHAT_CLIP = re.compile(r"\b(clip+|clippe[zr]?|clippez|clip it|clip ca|clip ça)\b", re.I)
# emote de chaîne Twitch : préfixe minuscule + suffixe en Majuscule (jdgFlou, zeratoRIRE…)
CHANNEL_EMOTE = re.compile(r"^[a-z][a-z0-9]{2,11}[A-Z][A-Za-z0-9]+$")

REACTIONS = ("rire", "choc", "hype", "malaise", "clip")


def classify_chat(text: str) -> set[str]:
    """Types de réaction présents dans un message de chat."""
    found: set[str] = set()
    tokens = [t for t in re.split(r"[\s,.!]+", text.strip()) if t]
    if CHAT_CLIP.search(text):
        found.add("clip")
    for token in tokens[:20]:
        if token.startswith("@"):
            continue
        if CHAT_LAUGH.match(token):
            found.add("rire")
        elif CHAT_SHOCK.match(token):
            found.add("choc")
        elif CHAT_HYPE.match(token):
            found.add("hype")
        elif CHAT_CRINGE.match(token):
            found.add("malaise")
    low = text.lower()
    if "?" * 3 in low or "!!!" in low:
        found.add("choc")
    return found


def emote_count(text: str) -> int:
    return sum(1 for t in text.split() if CHANNEL_EMOTE.match(t) or t in {"KEKW", "LUL", "OMEGALUL", "PogChamp", "Kappa", "monkaS"})


def chat_key(text: str) -> str:
    """Clé pour repérer les messages répétés / copypasta (lettres répétées compressées)."""
    key = normalize(text)
    key = re.sub(r"(.)\1{2,}", r"\1\1", key)
    return key[:60]
