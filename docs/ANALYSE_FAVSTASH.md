# Analyse du dépôt `alisadiq-ai/favstash-ai-video-editing-skills`

Analysé le 27/11/2026 (version 0.10.0, licence Apache-2.0, 16 skills).

## Ce que contient le dépôt

| Étape | Skills | Pertinence pour l'usine à clips |
|---|---|---|
| 1-research | `find-ideas` | Faible (idées de contenu original) |
| 2-scripting | `write-script` | Faible ; ses « hook levers » ont inspiré le prompt d'accroche |
| **3-editing** | **`clip-stream`**, **`edit-video`** | **Centrale** |
| 3-styles-* | 99 looks animés, personnages 3D/cartoon, styles « on camera » | Hors sujet (création de contenu original) |
| 4-publish-and-learn | `publish-and-analyze` | Publication via le service payant FavStash ; on utilise l'API TikTok officielle à la place |

## `clip-stream` — vérification du fonctionnement réel

Code : ~1 100 lignes de JavaScript (Node ≥ 22) + FFmpeg + streamlink.

- `find-moments.mjs` : signaux par seconde → `moments.json`.
  - **volume** (RMS FFmpeg par seconde, z-score robuste médiane/MAD sur 60 s, + écart au 80ᵉ percentile) ;
  - **parole** (transcription HyperFrames : expressions « clip that », « no way »…, rires, débit) ;
  - **chat** (débit de messages + emotes KEKW/POG/LUL, décalé de 5 s) ;
  - **coupures de scène** (optionnel).
  - Sélection de pics non chevauchants, fenêtre recalée sur les fins de phrase.
- `cut-clips.mjs` : 3 layouts 9:16 (`center`, `fill` flouté, `split` facecam en haut).
- `watch-live.mjs` : enregistre via streamlink, lit le chat IRC anonyme, passe toutes les 8 min.

**Tests exécutés : 18/18 passent** (`node --test tests/clip-stream*.test.mjs`).

### Défauts constatés (vérifiés par exécution)

1. **Le français est cassé.** `normalizeWords` applique `[^a-z0-9' ]` → « énorme » devient
   « norme », « oh là là » devient « oh l l ». Les mots-clés accentués ne matchent jamais.
2. **Lexique 100 % anglophone** : `MDR`, `PTDR`, `jpp`, `xptdr`, `ahahah` ne comptent pas comme
   réactions (testé : dans un burst « MDR, PTDR, jpp, KEKW », seul « KEKW » est reconnu).
3. **Aucune compréhension du contexte** : la raison est une phrase générée par règles
   (« Louder than usual, chat sped up ») ; pas de score de viralité, pas de titre.
4. **Facecam manuelle** : `--cam x:y:w:h` à mesurer à la main pour chaque streamer.
5. **Pas de sous-titres** dans `clip-stream` (renvoyé à `edit-video` + HyperFrames, workflow
   piloté par un agent, pas automatisé).
6. **Un seul stream à la fois**, pas de détection de lancement de live, pas de base de données,
   pas de file de validation, pas d'anti-doublon.

## `edit-video` — ce qui a été repris

C'est un guide pour agent (pas un programme) : règles de cadrage 9:16, **zone sûre** (texte entre
x=72..900 et y=269..1248 à 1080×1920), sous-titres 46–68 px centrés sur x=540, accroche dans la
première seconde, son normalisé, revue de l'export encodé. Ces règles sont appliquées
automatiquement par `clipfactory/media/render.py` et `captions.py`.

## Ce que Clip Factory reprend et corrige

| clip-stream | Clip Factory |
|---|---|
| Signaux volume / parole / chat | Portés en Python (numpy), **lexique FR** calibré sur du vrai chat FR, signal de **répétitions/copypasta** et d'emotes de chaîne, typologie rire/choc/hype/malaise |
| Raison générée par règles | **Claude** lit transcription + chat et explique *pourquoi*, choisit le contexte, note /100 (repli heuristique gratuit) |
| Layout manuel | **Détection automatique** de la facecam (OpenCV), suivi horizontal du visage en plein cadre |
| Pas de sous-titres | Sous-titres **mot à mot** synchronisés (Whisper FR), accroche, crédit, suppression des blancs |
| 1 stream, CLI | Démon 24/7 multi-streams, API Helix, base SQLite, tableau de bord, planning, API TikTok |
| Log streamlink | Même technique d'alignement chat ↔ vidéo malgré les pubs, **vérifiée sur streamlink 8.6** |

Les règles « droits d'abord » du skill (ne clipper qu'avec l'accord du streamer) sont
**appliquées dans le code** : sans autorisation enregistrée, la publication est bloquée.

## Utiliser les skills d'origine avec Claude Code

Les skills restent utiles en complément, pour retravailler à la main un clip déjà détecté :

```bash
git clone https://github.com/alisadiq-ai/favstash-ai-video-editing-skills ~/favstash
mkdir -p .claude/skills && cp -r ~/favstash/skills/3-editing/{clip-stream,edit-video} .claude/skills/
```

Le skill projet `.claude/skills/clip-factory/` explique à Claude Code comment piloter ce système.
