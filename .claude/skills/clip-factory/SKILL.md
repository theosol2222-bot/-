---
name: clip-factory
description: "Pilote l'usine à clips Twitch FR → TikTok de ce dépôt : diagnostiquer, régler la détection, revoir des clips, ajouter un streamer, lancer un replay de test. À utiliser quand on demande d'ajuster les clips, d'ajouter/retirer un streamer, de comprendre pourquoi un moment a (ou n'a pas) été clippé, ou de vérifier l'installation."
---

# Clip Factory

## Commandes

| But | Commande |
|---|---|
| Vérifier l'installation | `python -m clipfactory doctor` |
| Logins Twitch / qui est en live | `python -m clipfactory verify` |
| Tout lancer (24/7) | `python -m clipfactory run` |
| Tester sur un fichier comme un live | `python -m clipfactory replay FICHIER --login LOGIN --chat CHAT --speed 6` |
| Traiter une VOD autorisée | `python -m clipfactory process FICHIER --login LOGIN [--chat chat.json]` |
| Tests | `pytest` (rapides) · `pytest -m e2e` (faux live, ~1 min) |

## Comprendre un clip

Chaque clip en base (`data/clipfactory.db`, table `clips`) garde : `signals` (loud, chat,
speech, réaction dominante, expressions repérées, mentions d'autres streamers), `reason`
(pourquoi), `analysis.engine` (claude-… ou heuristique), `analysis.words` (transcription
horodatée). Commencer par ces champs avant de changer un réglage.

## Régler (une chose à la fois, dans `config/settings.yaml`)

- Trop de clips faibles → monter `analysis.min_viral_score` (55) ou `analysis.min_signal`.
- Moments ratés → ajouter des `mots_cles` au streamer (répliques cultes, noms de jeux), baisser `min_signal`.
- Chute coupée → `analysis.context_after` ; contexte manquant → `context_before`.
- Facecam mal détectée → `facecam: "x:y:w:h"` sur le streamer (mesurer sur une image du live).
- Lexique FR (rires, choc, hype…) : `clipfactory/detect/lexicon.py` ; ajouter un test dans `tests/test_detection.py`.

## Règles

- Ne jamais passer un streamer en `autorisation` autre que `aucune` sans preuve fournie par
  l'utilisateur (règlement de programme, accord écrit) : c'est ce qui débloque la publication.
- Les secrets restent dans `.env` ; ne jamais les afficher ni les committer.
- Pour retoucher un clip à la main, les skills FavStash `clip-stream` / `edit-video`
  (voir `docs/ANALYSE_FAVSTASH.md`) s'appliquent au fichier `clips/...mp4` produit.
