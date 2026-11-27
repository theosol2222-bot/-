# Coûts de fonctionnement

Estimations au 27/11/2026. **Vérifiez les prix en direct** avant de commander : les hébergeurs ont
relevé leurs tarifs en 2026 et les sources se contredisent.

## Postes gratuits

| Poste | Coût |
|---|---|
| API Twitch Helix, chat anonyme | 0 € |
| streamlink, FFmpeg, faster-whisper, OpenCV | 0 € (open source) |
| API TikTok Content Posting / Display | 0 € |

## Calcul (mesuré)

Mesures sur la machine de développement (4 vCPU, sans GPU) :

| Opération | Coût mesuré |
|---|---|
| Volume audio sur 4 min de live | 1,0 s (≈ 2,5 s pour 10 min) |
| Transcription Whisper `small` d'un candidat (85 s d'audio) | 6,3 s |
| Montage d'un clip de 35 s en 1080×1920 (x264 medium, détection visage et miniature incluses) | 23 s |
| Pipeline complet sur 4 min de live (4 candidats, 1 clip) | ~40 s |

Un live produit au plus 3 candidats par passe de 5 min, soit ~36 transcriptions/heure
(~4 min de calcul/heure sur 4 vCPU) + quelques montages. **Une machine 8 cœurs suit 4 lives simultanés.**
Disque : un live 720p60 ≈ 2 Go/heure, supprimé 6 h après la fin (`recording.keep_raw_hours`) ;
prévoir 100-200 Go.

## Hébergement (24/7)

| Option | Ordre de grandeur | Remarque |
|---|---|---|
| PC personnel allumé en permanence | électricité ~10-15 €/mois | Le moins cher ; dépend de votre connexion (4 lives ≈ 15-25 Mb/s descendants) |
| VPS 8 vCPU / 16 Go | ~20-50 €/mois | Vérifier le trafic inclus |
| Serveur dédié (ex. Hetzner AX42) | 46 €/mois au lancement en 2024 ; ~97 €/mois annoncé pour les nouvelles commandes à partir de juin 2026 ([page officielle](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/), chiffres contradictoires — à vérifier) | Confortable pour 6+ lives |

## Claude (analyse du contexte)

Par appel : ~3 000 jetons en entrée (transcription + chat), ~500-1 500 en sortie.

| Modèle (`llm.model`) | Prix / M jetons (entrée / sortie) | Coût par moment | 180 moments/jour |
|---|---|---|---|
| `claude-opus-5-5` (défaut) | 4 $ / 20 $ | ~0,02-0,04 $ | **~110-220 $/mois** |
| `claude-sonnet-5-5` | 2 $ / 10 $ | ~0,01-0,02 $ | ~55-110 $/mois |
| `claude-haiku-5-5` | 0,10 $ / 0,50 $ | ~0,001 $ | **~3-6 $/mois** |

Garde-fous déjà en place : pré-filtre heuristique gratuit (`llm.min_prefilter_score: 40`) et
plafond de `llm.max_calls_per_hour: 30`. Sans clé API, l'analyse heuristique fonctionne à 0 €.

**Le levier principal est le choix du modèle** : passer `llm.model` à `claude-haiku-5-5` divise
la facture par ~40, au prix d'une compréhension moins fine des blagues et du contexte. Conseil :
démarrer en Opus sur un ou deux streamers pendant une semaine, comparer les clips validés, puis
décider.

## Totaux mensuels indicatifs (13 streamers, ~4 lives simultanés aux heures de pointe)

| Configuration | Total |
|---|---|
| PC perso + heuristique seule | ~10-15 € |
| VPS + Claude Haiku 5.5 | ~25-55 € |
| VPS + Claude Opus 5.5 | ~130-270 € |

Le tableau de bord (onglet **Coûts**) suit la dépense Claude réelle jour par jour ; renseignez
`costs.server_monthly_eur` pour le total.
