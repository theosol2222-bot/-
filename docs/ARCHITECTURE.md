# Architecture

```
                 ┌──────────────── API Helix (officielle, 1 req/min) ───────────────┐
                 │                                                                    │
  config/streamers.yaml ──► Monitor (asyncio, 24/7) ──► StreamWorker × N lives        │
                 │                 │                     │  streamlink → stream.ts     │
                 │                 │                     │  ChatHub (WebSocket anonyme, toutes les chaînes)
                 │                 │                     ▼
                 │                 │        passe d'analyse toutes les 5 min (chevauchement 90 s)
                 │                 │                     │
                 │                 │   ① signaux bon marché sur tout le live : volume + chat (FR)
                 │                 │   ② top 3 pics → Whisper FR sur ~85 s autour de chaque pic
                 │                 │   ③ heuristique gratuite → si ≥ 40 : Claude (pourquoi, contexte, score /100, textes)
                 │                 │   ④ anti-doublon (chevauchement + empreinte de transcription)
                 │                 │   ⑤ score ≥ 55 → montage FFmpeg 9:16 (facecam auto, sous-titres, accroche, -14 LUFS)
                 │                 ▼
                 │           SQLite (WAL) ◄──── Tableau de bord FastAPI (validation, planning, stats, coûts)
                 │                 │
                 │                 ▼
                 └── Publication : créneaux FR, quotas, espacement ─► API TikTok (brouillon ou direct)
```

## Modules

| Fichier | Rôle |
|---|---|
| `clipfactory/monitor.py` | Démon : sondage Helix, un worker par live, file de calcul bornée, nettoyage disque, publication |
| `clipfactory/twitch/helix.py` | API officielle (jeton d'application client_credentials) |
| `clipfactory/twitch/chat.py` | Chat anonyme en lecture seule, une connexion pour toutes les chaînes, aucun pseudo stocké |
| `clipfactory/twitch/recorder.py` | streamlink + alignement chat ↔ vidéo malgré les pubs (EXT-X-PROGRAM-DATE-TIME) |
| `clipfactory/detect/lexicon.py` | Lexique FR streamer et chat (mdr, ptdr, jpp, emotes de chaîne…) |
| `clipfactory/detect/signals.py` | Signaux par seconde, z-score robuste, pics, réaction dominante |
| `clipfactory/detect/analyzer.py` | Analyse Claude (sortie structurée Pydantic) ou heuristique ; score viral /100 |
| `clipfactory/media/transcribe.py` | faster-whisper (CPU int8) en français, horodatage au mot |
| `clipfactory/media/face.py` | Détection facecam / visage (OpenCV) → layout `split`, `center` suivi, ou `fill` |
| `clipfactory/media/render.py` | Montage FFmpeg une passe : coupes, layout, ASS, loudnorm, miniature |
| `clipfactory/media/captions.py` | Sous-titres mot à mot surlignés, accroche, crédit (zone sûre TikTok) |
| `clipfactory/publish/*` | Métadonnées, créneaux, anti-doublon, workflow de validation, client TikTok |
| `clipfactory/web/*` | API JSON + page unique |

## Score de potentiel viral (/100)

```
score = 100 × [ w × contenu + (1 − w) × (0,40 × intensité + 0,35 × chat + 0,25 × voix) ]
  w = 0,45 avec Claude (contenu = note de Claude), 0,35 en heuristique
  intensité = 1 − e^(−signal/15)   chat = 1 − e^(−chat/6)   voix = 1 − e^(−(parole + 0,25·volume)/6)
  −10 si pas compréhensible seul · −8 si durée hors 15–99 s · +4 si un autre streamer suivi est mentionné
  plafonné à 35 si le moment est jugé sans intérêt
```

Le volume a un poids faible car toute phrase après un silence est « forte » ; ce qui distingue un
vrai moment, c'est la réaction (chat, rires, expressions fortes) et la compréhension du contenu.
Mesure sur le faux live de test : vrai moment 76–85/100, phrases calmes 31–35/100.

## Choix techniques comparés

| Besoin | Retenu | Alternatives écartées |
|---|---|---|
| Détection des lives | **Helix GET /streams** (gratuit, 100 chaînes/requête) | EventSub WebSocket (plus réactif mais 1 abonnement/chaîne, reconnexions) ; scraping (fragile, contraire aux CGU) |
| Enregistrement | **streamlink** (retire les pubs, journal par segment) | yt-dlp (pubs incluses, timing du chat approximatif) |
| Chat | **IRC WebSocket anonyme** | API EventSub chat (nécessite un compte/jeton utilisateur par chaîne) |
| Transcription | **faster-whisper small int8 CPU** (gratuit, local, bon en FR) | API Whisper/Deepgram (payant à l'heure, envoie l'audio à un tiers) ; `medium` (+qualité, ×2,5 CPU) |
| Compréhension | **Claude** (sortie structurée, refus → repli heuristique) | Règles seules (pas de « pourquoi ») |
| Visage | **OpenCV Haar** (livré avec OpenCV, 0 téléchargement) | MediaPipe/YuNet (plus précis, modèle à télécharger) — remplaçable dans `face.py` |
| Montage | **FFmpeg + libass** une passe | MoviePy (lent), HyperFrames/Remotion (Node + navigateur, lourd en 24/7) |
| Stockage | **SQLite WAL** | PostgreSQL (inutile à cette échelle) |
| Publication | **TikTok Content Posting API** officielle | Automatisation navigateur (interdite par les CGU, risque de bannissement) ; FavStash (payant) |

## Mise à l'échelle

- Un processus gère plusieurs lives : l'enregistrement est I/O, le calcul passe par un pool borné
  (`render.concurrency`). Au-delà de ~6 lives simultanés, lancer plusieurs instances avec des
  fichiers `streamers.yaml` différents et un `data_dir` partagé n'est pas recommandé (SQLite) :
  préférer une instance par machine et des bases séparées.
- Un GPU NVIDIA accélère Whisper (×10) : remplacer `device="cpu"` par `"cuda"` dans `transcribe.py`.
