# Installation

## 1. Prérequis

- Linux (Ubuntu 22.04+/Debian 12) ou macOS ; Python ≥ 3.11 ; FFmpeg avec libass ; 8 cœurs et
  16 Go de RAM recommandés pour 4 lives simultanés ; 100-200 Go de disque.
- Polices : Inter (`apt install fonts-inter`) — sinon libass prend une police de remplacement.

```bash
sudo apt install -y ffmpeg fonts-inter python3-venv git
git clone <ce dépôt> /opt/clipfactory && cd /opt/clipfactory
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # [dev] = outils de test (pytest, voix Piper, image de test)
```

> OpenCV doit rester en 4.x (`opencv-python-headless<5`) : OpenCV 5 a retiré le détecteur de
> visage utilisé. C'est déjà contraint dans `pyproject.toml`.

## 2. Identifiants (fichier `.env`)

```bash
cp .env.example .env && chmod 600 .env
```

| Variable | Où l'obtenir | Obligatoire |
|---|---|---|
| `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` | [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) → « Register Your Application », catégorie *Application Integration*, URL de redirection `http://localhost` | Oui |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | Non (sinon analyse heuristique) |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | [developers.tiktok.com](https://developers.tiktok.com) → créer une app, ajouter *Login Kit* + *Content Posting API*, scopes `user.info.basic, video.upload, video.publish, video.list`, URL de redirection = `TIKTOK_REDIRECT_URI` | Non (mode manuel) |
| `DASHBOARD_PASSWORD` | à choisir | Oui si le tableau de bord est exposé |

Les secrets ne sont jamais écrits dans la base ni dans les logs. Le jeton TikTok est stocké dans
`data/secrets/tiktok.json` (permissions 600). `.env` et `data/` sont ignorés par git.

## 3. Vérifier

```bash
python -m clipfactory doctor     # binaires, modules, secrets, autorisations
python -m clipfactory verify     # chaque login Twitch existe ? (API officielle) qui est en live ?
pytest                           # 33 tests (le test e2e nécessite l'étape 4)
```

Corrigez les logins signalés `INTROUVABLE` dans `config/streamers.yaml` (le login Twitch de
Mister V notamment n'a pas pu être confirmé lors du développement).

## 4. Valider la chaîne sur le faux live (MVP, sans Twitch)

```bash
# voix FR locale pour le générateur de test
mkdir -p data/models/piper && cd data/models/piper
for f in fr_FR-siwis-medium.onnx fr_FR-siwis-medium.onnx.json; do
  curl -sSLO "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/$f"; done
cd ../../..
python tests/fixtures/make_fake_stream.py data/test     # 4 min de faux live + chat FR simulé
pytest -m e2e                                           # le moment fort est trouvé, monté, noté ≥ 65
python -m clipfactory dashboard &                        # http://127.0.0.1:8080
python -m clipfactory replay data/test/stream.mp4 --chat data/test/chat.jsonl --login zerator --speed 4
```

Le replay exécute **la vraie boucle live** (passes incrémentales, chat, anti-doublon) sur le
fichier ; ouvrez l'onglet « À valider ».

## 5. MVP avec un seul streamer, puis extension

1. Dans `config/streamers.yaml`, passez tous les streamers à `actif: false` sauf un.
2. `python -m clipfactory run` (moniteur + tableau de bord). Le live est détecté dans la minute,
   enregistré, analysé toutes les 5 minutes ; les clips arrivent dans « À valider ».
3. Après quelques lives, ajustez : `analysis.min_viral_score`, `mots_cles` du streamer, `layout`.
4. Réactivez les streamers par lots ; surveillez CPU et disque (onglet Coûts) ;
   `monitor.max_parallel_streams` borne la charge.

## 6. Publication

1. Renseignez l'`autorisation` du streamer (voir `docs/MONETISATION.md`) — sinon la validation
   est refusée avec un message explicite.
2. Mode `manuel` (défaut) : « Valider & programmer » place le clip au prochain créneau FR libre ;
   à l'heure dite il passe « À publier », téléchargez-le et publiez depuis l'appli.
3. Mode `brouillon_tiktok` : connectez le compte (Planning → « Connecter TikTok ») ; la vidéo
   arrive dans la boîte de réception de l'appli TikTok à l'heure prévue.
4. Mode `direct` : après l'audit de votre app TikTok (sinon les posts restent privés).

Les règles d'espacement (`publish.min_gap_minutes`, `max_per_day`, `same_streamer_gap_hours`)
s'appliquent dans tous les modes.

## 7. Production 24/7

**Docker :**
```bash
cd deploy && docker compose up -d --build && docker compose logs -f
```
**systemd :** copier `deploy/clipfactory.service` dans `/etc/systemd/system/`, créer
l'utilisateur `clip`, puis `sudo systemctl enable --now clipfactory`.

Le tableau de bord écoute sur `127.0.0.1` : accédez-y par tunnel SSH
(`ssh -L 8080:localhost:8080 serveur`) ou derrière un reverse-proxy HTTPS (Caddy) avec
`DASHBOARD_PASSWORD`. Sauvegardez `data/clipfactory.db` et `data/secrets/`.

## Dépannage

| Symptôme | Cause / solution |
|---|---|
| `Twitch : Jeton Twitch refusé` | Identifiants `.env` erronés |
| Pas de clip alors que le chat s'emballe | Baisser `analysis.min_signal` ou `min_viral_score` ; vérifier le journal |
| Visage mal cadré | Forcer `facecam: "x:y:w:h"` (pixels source) ou `layout: fill` pour ce streamer |
| `module 'cv2' has no attribute 'CascadeClassifier'` | OpenCV 5 installé : `pip install "opencv-python-headless<5"` |
| Transcription lente | `analysis.whisper_model: base`, ou GPU (`device="cuda"`) |
