"""Génère un faux live de test (4 min) : gameplay synthétique 1280x720, facecam
en bas à gauche (photo NASA, domaine public, livrée avec scikit-image), voix FR
(Piper, local) avec un moment fort vers 2:30, et un chat FR simulé.

    python tests/fixtures/make_fake_stream.py data/test

Écrit stream.mp4, chat.jsonl ([{t, text}]) et truth.json (le moment attendu).
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "data" / "models" / "piper" / "fr_FR-siwis-medium.onnx"
DURATION = 240

SCRIPT = [  # (t, texte, gain dB)
    (6, "Salut tout le monde, bienvenue sur le live ! Aujourd'hui on teste la nouvelle map.", 0),
    (22, "Alors, il paraît qu'il y a un passage secret quelque part derrière la cascade.", 0),
    (40, "Bon, je vais prendre à gauche, normalement c'est par là.", 0),
    (62, "Merci pour les abonnements les gars, ça fait plaisir.", 0),
    (85, "Hum, je suis pas sûr, on va regarder la carte deux secondes.", 0),
    (108, "Attention, je crois qu'il y a quelqu'un derrière le mur, je l'entends.", 0),
    (126, "Je vais tenter un truc complètement fou, regardez bien, je saute depuis la tour.", 0),
    (146, "Non non non ! C'est pas possible ! Vous avez vu ça ? Il a fait quoi ?", 12),
    (154, "Ahahaha ! Je suis plié, c'est une dinguerie ! Clippez ça !", 10),
    (165, "Bon, on se calme, on reprend tranquillement.", 0),
    (190, "On va aller vers le château maintenant, il reste deux quêtes.", 0),
    (214, "Bon, je vais boire un verre d'eau et je reviens.", 0),
]
CALM = ["salut", "bonjour", "yo", "ça va ?", "gg", "c'est quoi la map ?", "il est où le passage", "lol", "trop bien",
        "first", "tu joues à quoi", "la musique est cool", "Kappa", "pk il va à gauche", "go droite"]
BURST = ["MDRRRRR", "PTDRRR", "KEKW", "KEKW", "CLIP", "CLIP ÇA", "c'est quoi ça ???", "WTF", "NON", "mdrrr",
         "jpp", "xptdr", "AHAHAHAHA", "jdgFlou jdgFlou", "il a fait quoi ???", "LUL", "OMEGALUL", "💀💀💀",
         "le saut de l'année", "masterclass"]


def sh(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True)


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    from skimage import data
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        Image.fromarray(data.astronaut()).resize((260, 260)).save(tmp / "face.png")
        inputs, filters = [], []
        for i, (t, text, gain) in enumerate(SCRIPT):
            wav = tmp / f"l{i}.wav"
            subprocess.run([sys.executable, "-m", "piper", "-m", str(VOICE), "-f", str(wav)], input=text.encode(),
                           check=True, capture_output=True)
            inputs += ["-i", str(wav)]
            filters.append(f"[{i}:a]volume={gain}dB,adelay={t * 1000}|{t * 1000},apad[s{i}]")
        n = len(SCRIPT)
        mix = "".join(f"[s{i}]" for i in range(n))
        sh(["ffmpeg", "-y", "-v", "error", *inputs,
            "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.015:duration={DURATION}",
            "-filter_complex", ";".join(filters) + f";{mix}[{n}:a]amix=inputs={n + 1}:normalize=0:duration=longest,"
            f"atrim=0:{DURATION}[a]", "-map", "[a]", "-ar", "48000", "-ac", "2", str(tmp / "voice.wav")])
        sh(["ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={DURATION}",
            "-loop", "1", "-i", str(tmp / "face.png"), "-i", str(tmp / "voice.wav"),
            "-filter_complex",
            "[0:v]hue=h=t*8,eq=saturation=0.7[bg];[1:v]scale=260:260,format=yuv420p[cam];"
            "[bg][cam]overlay=x=24+4*sin(t):y=H-h-24:shortest=1[v]",
            "-map", "[v]", "-map", "2:a", "-t", str(DURATION), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "26", "-c:a", "aac", "-b:a", "128k", str(out_dir / "stream.mp4")])

    rnd = random.Random(42)
    chat = []
    t = 0.0
    while t < DURATION:
        t += rnd.expovariate(1.2)
        chat.append({"t": round(t, 2), "text": rnd.choice(CALM)})
    for _ in range(140):  # le chat explose ~6 s après le moment (146 s)
        chat.append({"t": round(rnd.uniform(150, 166), 2), "text": rnd.choice(BURST)})
    chat.sort(key=lambda m: m["t"])
    (out_dir / "chat.jsonl").write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in chat) + "\n", encoding="utf-8")
    (out_dir / "truth.json").write_text(json.dumps({"moment": [140, 165], "peak": 148}), encoding="utf-8")
    print(f"Faux live écrit dans {out_dir}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data" / "test"))
