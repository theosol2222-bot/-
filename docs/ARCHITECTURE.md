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
| Visage | **YuNet** (zoo officiel OpenCV, modèle 230 Ko inclus), repli Haar | MediaPipe 1.2 (installe deux OpenCV 5 concurrents), YOLO/Ultralytics (AGPL-3.0) |
| Cadrage | **Caméra « trépied lourd »** reprise d'openshorts + recadrage net aux changements de plan (**PySceneDetect**) | Moyenne glissante (oscille, panoramique au changement de plan) |
| Montage | **FFmpeg + libass** une passe | MoviePy (lent), HyperFrames/Remotion (Node + navigateur, lourd en 24/7) |
| Stockage | **SQLite WAL** | PostgreSQL (inutile à cette échelle) |
| Publication | **TikTok Content Posting API** officielle | Automatisation navigateur (interdite par les CGU, risque de bannissement) ; FavStash (payant) |

## Mise à l'échelle

- Un processus gère plusieurs lives : l'enregistrement est I/O, le calcul passe par un pool borné
  (`render.concurrency`). Au-delà de ~6 lives simultanés, lancer plusieurs instances avec des
  fichiers `streamers.yaml` différents et un `data_dir` partagé n'est pas recommandé (SQLite) :
  préférer une instance par machine et des bases séparées.
- Un GPU NVIDIA accélère Whisper (×10) : remplacer `device="cpu"` par `"cuda"` dans `transcribe.py`.

## Dépôts GitHub évalués (27/11/2026)

Étoiles et date du dernier commit relevées via l'API GitHub.

| Dépôt | ★ | Dernier commit | Licence | Décision |
|---|---|---|---|---|
| SYSTRAN/faster-whisper | 27 061 | 25/11/2026 | MIT | **Utilisé** : transcription FR au mot |
| google-ai-edge/mediapipe | 37 890 | 26/11/2026 | Apache-2.0 | Testé, écarté : la v1.2 installe opencv-python + opencv-contrib 5.x en conflit ; YuNet donne le même service sans dépendance |
| opencv/opencv_zoo (YuNet) | 1 152 | 20/10/2026 | Apache-2.0 | **Utilisé** : détection de visage |
| mutonby/openshorts | 9 977 | 27/11/2026 | MIT | **Idées reprises** : caméra stabilisée (zone morte, confirmation des sauts, réinitialisation au changement de plan) |
| Breakthrough/PySceneDetect | 5 670 | 25/11/2026 | BSD-3 | **Utilisé** : changements de plan pour le cadrage |
| Anil-matcha/AI-Youtube-Shorts-Generator | 6 593 | 24/11/2026 | MIT | Étudié : pipeline YouTube → LLM → découpe, sans chat ni live ; rien de plus à reprendre |
| streamlink/streamlink | 12 002 | 24/11/2026 | BSD-2 | **Utilisé** : enregistrement, détection de live de repli |
| m-bain/whisperX | 25 735 | 21/11/2026 | BSD-2 | Option GPU : alignement au mot plus précis, mais impose PyTorch (~2 Go) — pas pour un petit serveur CPU |
| WyattBlue/auto-editor | 6 180 | 27/11/2026 | Unlicense | Écarté : la coupe des blancs est déjà faite sur les mots transcrits, plus précise pour la parole |
| Zulko/moviepy | 15 222 | 26/08/2026 | MIT | Écarté : bien plus lent que FFmpeg en une passe pour du 24/7 |
| remotion-dev/remotion | 76 094 | 27/11/2026 | spécifique | Écarté : Node + navigateur headless, licence payante pour les entreprises |
| ultralytics/ultralytics | 63 806 | 27/11/2026 | AGPL-3.0 | Écarté : licence AGPL contaminante, inutile pour des visages |
| harry0703/MoneyPrinterTurbo | 145 951 | 27/11/2026 | MIT | Hors sujet (génère des vidéos à partir de texte) |
| facebookresearch/demucs | 10 426 | 25/07/2024 | MIT | Écarté : plus maintenu ; aurait servi à isoler la voix de la musique du jeu |
| lay295/TwitchDownloader | 4 154 | 15/11/2026 | MIT | Complément pour les VOD : télécharge la rediffusion du chat → `clipfactory process --chat` |
