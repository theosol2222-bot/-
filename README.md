# Clip Factory — streams Twitch FR → clips TikTok

Système automatisé qui surveille des streamers Twitch francophones, détecte leurs meilleurs moments
en direct, **comprend pourquoi** ils sont intéressants, monte des vidéos 9:16 prêtes pour TikTok
et gère leur validation, leur planification et leur publication via les API officielles.

```
Lives Twitch ─► détection (volume + chat FR) ─► Whisper FR ─► Claude : pourquoi ? contexte ? score /100
   ─► montage 1080×1920 (facecam auto, sous-titres mot à mot, accroche, -14 LUFS) ─► validation ─► TikTok
```

## Ce qui est fait et testé

| Fonction | État |
|---|---|
| Liste de streamers modifiable à chaud (`config/streamers.yaml`, 13 préconfigurés) | ✅ |
| Détection des lancements de live (API Helix officielle ; repli streamlink sans identifiants) | ✅ repli testé sur Twitch réel ; Helix testé avec API simulée |
| Chat Twitch temps réel, multi-chaînes, sans pseudos | ✅ **testé sur le vrai chat** (Domingo, JL Tomy) |
| Enregistrement + alignement chat/vidéo malgré les pubs (streamlink) | ✅ format de log vérifié sur streamlink 8.6 |
| Signaux FR : pics de chat, répétitions, emotes, rires, expressions, volume | ✅ |
| Compréhension du contexte par Claude + score viral /100 (repli heuristique gratuit) | ✅ testé avec client simulé — à valider avec une clé API |
| Montage 9:16 : facecam détectée seule (YuNet), caméra stabilisée et recadrage aux changements de plan (openshorts, PySceneDetect), sous-titres dynamiques, coupe des blancs, accroche, crédit, miniature | ✅ testé de bout en bout |
| Anti-doublon (même live, autre POV), créneaux FR, quotas, espacement | ✅ |
| Publication TikTok (manuel / brouillon / direct) + statistiques | ✅ testé avec API simulée — nécessite votre app TikTok |
| Tableau de bord (lives, chat, clips, aperçus, validation, planning, stats, coûts) | ✅ |
| Blocage de publication sans autorisation du streamer | ✅ |

**37 tests automatisés**, dont un test de bout en bout sur un faux live (voix FR, facecam, chat
simulé) : le vrai moment est noté 76-85/100 et monté, les passages calmes 31-35/100 et ignorés.

## Démarrage rapide

```bash
pip install -e ".[dev]"
cp .env.example .env && chmod 600 .env      # identifiants Twitch (gratuits), Claude et TikTok optionnels
python -m clipfactory doctor
python -m clipfactory run                   # moniteur 24/7 + tableau de bord http://127.0.0.1:8080
```

## Documentation

- [Installation, MVP 1 streamer, production](docs/INSTALLATION.md)
- [Architecture, score viral, choix techniques comparés](docs/ARCHITECTURE.md)
- [Analyse du dépôt FavStash (clip-stream, edit-video)](docs/ANALYSE_FAVSTASH.md)
- [Coûts mensuels](docs/COUTS.md)
- [Monétisation et droits en France](docs/MONETISATION.md)

## Droits

Le code n'autorise la publication que pour les streamers dont l'autorisation est renseignée
(programme de clipping officiel, accord écrit, propre chaîne). La détection et le montage
fonctionnent sans, pour tester et préparer une proposition aux streamers.

Détection et layouts dérivés de [`clip-stream`](https://github.com/alisadiq-ai/favstash-ai-video-editing-skills)
(FavStash, Apache-2.0) — voir [NOTICE](NOTICE).
