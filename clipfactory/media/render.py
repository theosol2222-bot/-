"""Montage 9:16 automatique d'un moment.

Chaîne FFmpeg en une passe : coupe des temps morts (concat de segments) →
layout (split facecam / center qui suit le visage / fill flouté) → sous-titres
ASS dynamiques + accroche + crédit → normalisation audio EBU R128 (-14 LUFS).
Les layouts reprennent ceux de clip-stream (FavStash, Apache-2.0).
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..detect.signals import Word
from . import face as face_mod
from .captions import CaptionStyle, build_ass, build_thumbnail_ass
from .ffmpeg import loudness, probe, run

log = logging.getLogger(__name__)
OUT_W, OUT_H = 1080, 1920


def even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


# ---------------------------------------------------------------------------
# Temps morts
# ---------------------------------------------------------------------------
def keep_segments(start: float, end: float, words: list[Word], db: np.ndarray | None = None,
                  min_gap: float = 1.2, pad: float = 0.25, lead_max: float = 1.0) -> list[tuple[float, float]]:
    """Segments à garder dans [start, end] : on retire les silences de parole
    longs, sauf s'il s'y passe quelque chose de fort à l'audio (cri, explosion,
    rire sans mots). `db` = dB par seconde depuis `start`."""
    inside = [w for w in words if w.end > start and w.start < end]
    if not inside:
        return [(start, end)]
    loud_ref = float(np.percentile(db, 75)) if db is not None and len(db) else None

    def eventful(a: float, b: float) -> bool:
        if db is None or loud_ref is None:
            return False
        i, j = int(max(0, a - start)), int(min(len(db), b - start + 1))
        return j > i and float(np.max(db[i:j])) > loud_ref + 4

    cuts: list[tuple[float, float]] = []
    first = inside[0].start
    if first - start > lead_max + pad and not eventful(start, first):
        cuts.append((start, first - pad))  # accroche : on démarre sur la parole
    for a, b in zip(inside, inside[1:]):
        if b.start - a.end >= min_gap and not eventful(a.end, b.start):
            cuts.append((a.end + pad, b.start - pad))
    segments, cursor = [], start
    for a, b in cuts:
        if a - cursor > 0.05:
            segments.append((cursor, a))
        cursor = b
    if end - cursor > 0.05:
        segments.append((cursor, end))
    return segments or [(start, end)]


def remap(t: float, segments: list[tuple[float, float]]) -> float | None:
    """Temps source absolu → temps de sortie (None si coupé)."""
    acc = 0.0
    for a, b in segments:
        if t < a:
            return None
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return None


def remap_words(words: list[Word], segments: list[tuple[float, float]]) -> list[Word]:
    out = []
    for w in words:
        s, e = remap(w.start, segments), remap(w.end, segments)
        if s is None and e is None:
            continue
        if s is None:
            s = max(0.0, e - (w.end - w.start))
        if e is None:
            e = s + (w.end - w.start)
        out.append(Word(w.text, round(s, 3), round(e, 3)))
    return out


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------
def cover_crop(src_w: int, src_h: int, out_w: int, out_h: int, focus: float | str = 0.5) -> str:
    scale = max(out_w / src_w, out_h / src_h)
    w, h = even(src_w * scale), even(src_h * scale)
    y = (h - out_h) // 2
    if isinstance(focus, str):  # expression temporelle (suivi du visage)
        x = f"'max(0,min({w - out_w},{focus}))'"
    else:
        x = str(int(round((w - out_w) * min(1, max(0, focus)))))
    return f"scale={w}:{h}:flags=lanczos,crop={out_w}:{out_h}:{x}:{y}"


def tracking_expr(points: list[tuple[float, float]]) -> str:
    """Interpolation linéaire par morceaux de x(t) pour `crop` (suivi du visage)."""
    if len(points) == 1:
        return f"{points[0][1]:.1f}"
    expr = f"{points[-1][1]:.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(points, points[1:]))):
        dt = max(1e-3, t1 - t0)
        expr = f"if(lt(t,{t1:.2f}),{x0:.1f}+({x1 - x0:.1f})*(t-{t0:.2f})/{dt:.2f},{expr})"
    return f"if(lt(t,{points[0][0]:.2f}),{points[0][1]:.1f},{expr})"


@dataclass
class Layout:
    kind: str = "fill"
    cam: tuple[int, int, int, int] | None = None
    focus: float = 0.5
    track: list[tuple[float, float]] = field(default_factory=list)  # (t_sortie, centre_x 0..1)
    cam_height: int = 760


def layout_graph(lay: Layout, src_w: int, src_h: int, inp: str = "[vc]", out: str = "[lv]") -> str:
    if lay.kind == "center":
        focus: float | str = lay.focus
        if len(lay.track) >= 2:
            scale = max(OUT_W / src_w, OUT_H / src_h)
            sw = even(src_w * scale)
            pts = [(t, cx * sw - OUT_W / 2) for t, cx in lay.track]
            focus = tracking_expr(pts)
        return f"{inp}{cover_crop(src_w, src_h, OUT_W, OUT_H, focus)},setsar=1{out}"
    if lay.kind == "split" and lay.cam:
        cam_h = even(min(OUT_H * 0.5, max(480, lay.cam_height)))
        game_h = OUT_H - cam_h
        x, y, w, h = lay.cam
        x, y = min(x, src_w - 2), min(y, src_h - 2)
        w, h = even(min(w, src_w - x)), even(min(h, src_h - y))
        return (f"{inp}split=2[camsrc][gamesrc];"
                f"[camsrc]crop={w}:{h}:{x}:{y},{cover_crop(w, h, OUT_W, cam_h)}[cam];"
                f"[gamesrc]{cover_crop(src_w, src_h, OUT_W, game_h, 0.5)}[game];"
                f"[cam][game]vstack=inputs=2,setsar=1{out}")
    fg_h = even(min(OUT_H, src_h / src_w * OUT_W * 1.25))
    fg_w = even(fg_h * src_w / src_h)
    return (f"{inp}split=2[bgsrc][fgsrc];"
            f"[bgsrc]{cover_crop(src_w, src_h, OUT_W // 4, OUT_H // 4)},boxblur=8:2,eq=brightness=-0.12:saturation=1.1,"
            f"scale={OUT_W}:{OUT_H}[bg];"
            f"[fgsrc]scale={fg_w}:{fg_h}:flags=lanczos,crop={min(fg_w, OUT_W)}:{fg_h}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1{out}")


def choose_layout(source: Path | str, start: float, end: float, segments: list[tuple[float, float]],
                  forced: str = "auto", facecam: str | None = None) -> tuple[Layout, face_mod.FaceInfo | None]:
    if facecam:
        parts = [int(float(p)) for p in str(facecam).replace(",", ":").split(":")]
        return Layout("split", tuple(parts)), None  # type: ignore[arg-type]
    info = face_mod.analyze(source, start, end)
    kind = info.layout if forced == "auto" else forced
    lay = Layout(kind, info.cam, info.focus)
    if kind == "center" and info.layout == "center":
        # caméra « trépied lourd » + recadrage net aux changements de plan
        src = probe(source)
        crop_w_src = src["height"] * OUT_W / OUT_H
        cuts = face_mod.scene_cuts(source, start, end)
        traj = face_mod.track(source, start, end, crop_w_src, src["width"], cuts)
        pts = [(remap(t, segments), cx / src["width"]) for t, cx in traj]
        lay.track = [(t, x) for t, x in pts if t is not None]
    if kind == "split" and not lay.cam:
        lay.kind = "fill"
    return lay, info


# ---------------------------------------------------------------------------
@dataclass
class RenderResult:
    file: Path
    thumbnail: Path | None
    duration: float
    layout: str
    segments: list[tuple[float, float]]
    removed_seconds: float


def render_clip(source: Path | str, start: float, end: float, words: list[Word], out: Path,
                hook: str | None, credit: str, title: str | None = None, peak: float | None = None,
                settings=None, layout: str = "auto", facecam: str | None = None) -> RenderResult:
    get = (lambda k, d: settings.get(k, d)) if settings else (lambda k, d: d)
    info = probe(source)
    if not info["width"]:
        raise ValueError("Pas de piste vidéo dans la source")
    db = loudness(source, start, end - start) if info["has_audio"] else None
    segments = (keep_segments(start, end, words, db, min_gap=float(get("render.silence_min_gap", 1.2)))
                if get("render.remove_silences", True) else [(start, end)])
    duration = sum(b - a for a, b in segments)
    lay, _ = choose_layout(source, start, end, segments, layout, facecam)
    style = CaptionStyle(font=get("render.font", "Inter"))
    hook_y, credit_y = 300, 215
    if lay.kind == "split":  # accroche sur la jonction cam/jeu, jamais sur le visage
        cam_h = even(min(OUT_H * 0.5, max(480, lay.cam_height)))
        hook_y, credit_y, style.y = cam_h + 50, cam_h - 40, cam_h + 400
    elif lay.kind == "center":  # caméra plein cadre : le visage occupe le haut, textes en dessous
        hook_y, credit_y, style.y = 1000, 215, 1200
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        ass = Path(tmp) / "subs.ass"
        ass.write_text(build_ass(remap_words(words, segments), duration, style, hook=hook, credit=credit,
                                  hook_y=hook_y, credit_y=credit_y), encoding="utf-8")
        # Entrée seekée sur `start - 1` (seek rapide), puis trims relatifs.
        base = max(0.0, start - 1.0)
        parts, labels = [], []
        for i, (a, b) in enumerate(segments):
            ra, rb = a - base, b - base
            parts.append(f"[0:v]trim=start={ra:.3f}:end={rb:.3f},setpts=PTS-STARTPTS[v{i}]")
            if info["has_audio"]:
                fade_out = max(0.0, rb - ra - 0.03)
                parts.append(f"[0:a]atrim=start={ra:.3f}:end={rb:.3f},asetpts=PTS-STARTPTS,"
                             f"afade=t=in:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.03[a{i}]")
                labels.append(f"[v{i}][a{i}]")
            else:
                labels.append(f"[v{i}]")
        n = len(segments)
        concat = f"{''.join(labels)}concat=n={n}:v=1:a={1 if info['has_audio'] else 0}[vc]" + ("[ac]" if info["has_audio"] else "")
        fps = min(60, info["fps"] or 30)
        ass_path = str(ass).replace("\\", "/").replace(":", "\\:")
        graph = ";".join(parts + [concat, layout_graph(lay, info["width"], info["height"]),
                                  f"[lv]ass='{ass_path}',fps={fps:.3f},format=yuv420p[v]"])
        if info["has_audio"]:
            lufs = float(get("render.loudness_lufs", -14))
            graph += f";[ac]loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000[a]"
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{base:.3f}", "-t", f"{end - base + 0.5:.3f}",
               "-i", str(source), "-filter_complex", graph, "-map", "[v]"]
        if info["has_audio"]:
            cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        cmd += ["-c:v", "libx264", "-preset", str(get("render.preset", "medium")), "-crf", str(get("render.crf", 20)),
                "-profile:v", "high", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
        run(cmd)

        thumb = None
        if title:
            thumb = out.with_suffix(".jpg")
            t_peak = remap(peak, segments) if peak is not None else None
            t_src = peak if t_peak is not None and peak is not None else start + (end - start) * 0.6
            tass = Path(tmp) / "thumb.ass"
            tass.write_text(build_thumbnail_ass(title, credit, style.font), encoding="utf-8")
            tpath = str(tass).replace("\\", "/").replace(":", "\\:")
            lay_static = Layout(lay.kind, lay.cam, lay.focus)
            tg = layout_graph(lay_static, info["width"], info["height"], "[0:v]", "[lv]")
            run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{t_src:.2f}", "-i", str(source),
                 "-filter_complex", f"{tg};[lv]eq=brightness=-0.08,ass='{tpath}'[v]", "-map", "[v]",
                 "-frames:v", "1", "-q:v", "2", str(thumb)])
    final = probe(out)
    return RenderResult(file=out, thumbnail=thumb, duration=final["duration"], layout=lay.kind,
                        segments=segments, removed_seconds=round((end - start) - duration, 2))
