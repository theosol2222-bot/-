# Monétisation et droits — marché français

Synthèse au 27/11/2026. Beaucoup de sources sur le clipping sont des blogs d'outils ou d'agences
qui ont intérêt à vendre le modèle : **vérifiez toujours les conditions sur la page officielle
du programme ou de la campagne.**

## 1. Le préalable : l'autorisation

Republier des extraits de live d'un streamer sans son accord est une contrefaçon (Code de la
propriété intellectuelle) et viole les CGU de Twitch et TikTok ; le streamer peut faire retirer
les vidéos et faire sanctionner le compte. Le système l'applique : **sans `autorisation` dans
`config/streamers.yaml`, rien n'est publié.** Voies légitimes :

| Voie | Comment | Statut dans la config |
|---|---|---|
| Programme de clipping officiel du streamer | Rejoindre son Discord / sa campagne, respecter ses règles (crédit, durée, plateformes) | `programme_officiel` + lien du règlement dans `preuve` |
| Accord direct | Mail/DM écrit du streamer ou de son agence (partage de revenus, compte dédié) | `accord_ecrit` + référence dans `preuve` |
| Votre propre chaîne | Vous clippez vos lives | `propre_chaine` |

Autres points de vigilance (signalés par Claude dans `risques`) : musique sous licence audible
(Content ID TikTok → muet/retrait), données personnelles à l'écran, propos sur des tiers.

## 2. Plateformes de campagnes rémunérées à la vue

Le modèle qui rapporte réellement aujourd'hui : un streamer, une marque ou une plateforme finance
une campagne, et paie les clippeurs aux vues vérifiées, après validation du clip.

| Plateforme | Ce qu'on sait | Accès depuis la France |
|---|---|---|
| **Content Rewards** (ex-« Whop Content Rewards », app propre depuis août 2026) | Paiement aux vues vérifiées ; une revue de 50 campagnes actives (sept. 2026) donne une **médiane de 1 $ / 1 000 vues** ; les blogs annoncent 1-5 $ pour les campagnes de streamers | Oui (paiement en ligne) |
| **Ssemble Clip Rewards** | Place de marché de campagnes de clipping, page en français | Oui |
| **Vyro** | ~3 $ / 1 000 vues annoncés (une seule source comparative) | À vérifier |

C'est la **meilleure opportunité réellement accessible** : les campagnes sont autorisées par
nature (l'autorisation vient avec la campagne), la rémunération ne dépend pas de l'éligibilité
de votre compte TikTok, et l'usine peut alimenter plusieurs comptes. Ordre de grandeur réaliste
à la médiane : 1 million de vues ≈ 1 000 $.

Méthode recommandée : pour chaque campagne acceptée, ajoutez le streamer dans `streamers.yaml`
avec `autorisation: programme_officiel` et le lien de la campagne dans `preuve`, adaptez
`render.min_duration/max_duration` et le crédit exigé, et ne soumettez que les clips validés.

## 3. Programmes officiels des streamers français

Je n'ai trouvé aucune campagne publique et vérifiable lancée par l'un des 13 streamers de la liste
à date. Les programmes de clippeurs existent surtout côté Kick et chez de grands créateurs
anglophones. Démarche concrète : contacter les streamers (ou leurs agences) avec une proposition
chiffrée — c'est aussi là que le tableau de bord sert : montrer des clips déjà montés, des
statistiques et proposer un partage de revenus ou une rémunération à la vue.

## 4. Partenariats avec des créateurs

- **Clippeur attitré / prestataire** : facturer le service de clipping au streamer (forfait mensuel
  ou à la vidéo), sur le compte TikTok du streamer — le plus simple juridiquement, et la
  monétisation TikTok revient au titulaire du compte.
- **Compte « fan » officiel co-géré** avec partage des revenus, encadré par un contrat écrit.
- Pour un auto-entrepreneur en France : déclarer l'activité (micro-entreprise, BNC/BIC selon le
  montage), les revenus de plateformes étrangères sont imposables.

## 5. Programme TikTok Creator Rewards

- La **France est éligible** ; conditions publiées : 18 ans, **10 000 abonnés**, vidéos de **plus
  d'une minute**, contenu **original**.
- **Point bloquant pour un compte de clips** : les sources concordent pour dire que les extraits
  repostés avec un apport minimal sont considérés comme « non originaux » et inéligibles, et que
  des violations répétées rendent le compte inéligible. Un clip ne peut être éligible que si le
  montage apporte une vraie valeur (commentaire, narration, mise en contexte) et dure > 1 min.
- Conclusion : **ne pas bâtir le modèle économique sur Creator Rewards** pour un compte de clips
  de tiers. Les campagnes à la vue (section 2) et les prestations (section 4) sont les vraies sources.

## 6. TikTok Content Posting API — contraintes de publication

- Tant que l'application n'a pas passé l'**audit** TikTok, les publications directes sont
  **privées uniquement** (erreur `unaudited_client_can_only_post_to_private_accounts`) et limitées
  à 5 utilisateurs par 24 h.
- D'où le mode par défaut `manuel`, puis `brouillon_tiktok` (la vidéo arrive dans l'appli, vous
  publiez en un geste, et vous pouvez ajouter un son tendance), puis `direct` après audit.

## Sources

- [TikTok — Content Posting API, Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)
- [TikTok — Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines)
- [Creator Rewards : la France est éligible (quasa.io)](https://quasa.io/fr/media/creator-rewards-tiktok-la-france-est-eligible-pas-toute-la-francophonie)
- [Buffer — TikTok Creator Rewards Program](https://buffer.com/resources/tiktok-creativity-program/)
- [FindClout — Creator Rewards et contenus repostés](https://findclout.com/blog/tiktok-creator-rewards-reposted-clips)
- [Docs Whop — Content Rewards](https://docs.whop.com/manage-your-business/growth-marketing/content-rewards)
- [Content Rewards — guide streamers](https://contentrewards.com/blog/get-paid-clip-streamers)
- [Builtbyfoundry — revue de 50 campagnes](https://www.builtbyfoundry.io/blog/what-is-clipping-1-dollar-per-1000-views-50-campaigns)
- [OpusClip — Vyro vs Whop](https://www.opus.pro/blog/vyro-vs-whop)
- [Ssemble — Clip Rewards](https://www.ssemble.com/fr/clip-rewards)
- [Cut.pro — rejoindre l'armée de clippeurs d'un streamer](https://cut.pro/fr/blog/rejoindre-armee-clippeurs-streamer-2026)
