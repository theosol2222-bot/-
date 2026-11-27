"""Sous-titres ASS dynamiques (mot courant surligné, petit « pop »), accroche et crédit."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..detect.signals import Word


@dataclass
class CaptionStyle:
    font: str = "Inter"
    size: int = 74
    y: int = 1150                 # centre vertical des sous-titres
    color: str = "&H00FFFFFF"     # blanc (ASS = AABBGGRR)
    highlight: str = "&H0000E5FF"  # jaune-orangé
    outline: int = 7
    max_words: int = 3
    max_chars: int = 20


def ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]+")


def esc(text: str) -> str:
    """Texte sûr pour ASS ; emoji retirés (police vidéo sans glyphes couleur)."""
    text = EMOJI.sub("", text)
    return re.sub(r"\s+", " ", text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")).strip()


def group_words(words: list[Word], max_words: int = 3, max_chars: int = 20, gap: float = 0.5) -> list[list[Word]]:
    groups: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        if current:
            text_len = sum(len(x.text) + 1 for x in current) + len(w.text)
            if (len(current) >= max_words or text_len > max_chars or w.start - current[-1].end > gap
                    or re.search(r"[.!?…,]$", current[-1].text)):
                groups.append(current)
                current = []
        current.append(w)
    if current:
        groups.append(current)
    return groups


def wrap_hook(text: str, width: int = 22) -> str:
    words, lines, line = esc(text).split(), [], ""
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return "\\N".join(lines[:3])


def build_ass(words: list[Word], duration: float, style: CaptionStyle, hook: str | None = None,
              credit: str | None = None, width: int = 1080, height: int = 1920, hook_seconds: float = 3.2,
              hook_y: int = 300, credit_y: int = 210) -> str:
    """`words` doivent déjà être sur la timeline de sortie (secondes depuis 0)."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{style.font},{style.size},{style.color},&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{style.outline},2,5,60,60,0,1
Style: Hook,{style.font},62,&H00111111,&H000000FF,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,18,0,8,90,90,300,1
Style: Credit,{style.font},34,&H40FFFFFF,&H000000FF,&H80000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,8,60,60,190,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    if credit:
        lines.append(f"Dialogue: 2,{ts(0)},{ts(duration)},Credit,,0,0,0,,{{\\an5\\pos({width // 2},{credit_y})}}{esc(credit)}")
    if hook:
        end = min(duration, hook_seconds)
        lines.append(f"Dialogue: 3,{ts(0)},{ts(end)},Hook,,0,0,0,,"
                     f"{{\\an8\\pos({width // 2},{hook_y})\\fad(0,180)\\t(0,140,\\fscx106\\fscy106)\\t(140,260,\\fscx100\\fscy100)}}"
                     f"{wrap_hook(hook)}")
    pos = f"\\pos({width // 2},{style.y})"
    groups = group_words([w for w in words if w.end > 0 and w.start < duration], style.max_words, style.max_chars)
    for gi, group in enumerate(groups):
        g_end = group[-1].end
        nxt = groups[gi + 1][0].start if gi + 1 < len(groups) else None
        if nxt is not None and nxt - g_end < 0.35:
            g_end = nxt
        g_end = min(duration, g_end + (0.25 if nxt is None or nxt - g_end >= 0.35 else 0))
        for wi, word in enumerate(group):
            start = max(0.0, word.start)
            end = group[wi + 1].start if wi + 1 < len(group) else g_end
            if end - start < 0.05:
                continue
            parts = []
            for k, other in enumerate(group):
                t = esc(other.text)
                parts.append(f"{{\\c{style.highlight}}}{t}{{\\c{style.color}}}" if k == wi else t)
            pop = "\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)" if wi == 0 else ""
            lines.append(f"Dialogue: 1,{ts(start)},{ts(end)},Cap,,0,0,0,,{{{pos}{pop}}}{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


def build_thumbnail_ass(title: str, credit: str, font: str = "Inter", width: int = 1080, height: int = 1920) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: T,{font},92,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,9,3,5,70,70,0,1
Style: C,{font},44,&H0000E5FF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,0,5,70,70,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,0:00:00.00,0:00:10.00,T,,0,0,0,,{{\\pos({width // 2},{int(height * 0.47)})}}{wrap_hook(title.upper(), 16)}
Dialogue: 1,0:00:00.00,0:00:10.00,C,,0,0,0,,{{\\pos({width // 2},{int(height * 0.62)})}}{esc(credit)}
"""
