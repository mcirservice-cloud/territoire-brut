# Territoire Brut — pipeline d'automatisation

Chaîne YouTube de documentaires animaliers en français, **100 % automatisée** :
2 vidéos longues par jour (4 à 12 min) + 3 Shorts extraits automatiquement.

## Ce que fait le pipeline

À chaque exécution (GitHub Actions, 05:00 et 14:00 UTC), le pipeline :

1. **Sujet** — prend le prochain sujet « à produire » du Google Sheet
   (ou de `topics.csv` en local).
2. **Faits** — récupère les faits vérifiables sur Wikipédia FR et GBIF
   (plafonnés à 9 000 caractères pour économiser les tokens).
3. **Script** — Gemini (`gemini-3.8-flash`, mode JSON, température 0,7)
   écrit le plan de montage complet avec le prompt maître
   (`config/prompts/script_master.txt`), avec validation structurelle
   (ids continus, max 20 % de clips IA, jamais deux animations
   identiques consécutives, clés de sons autorisées uniquement).
4. **Vérification** — Groq (`llama-3.1-8b-instant`) compare le script
   aux sources. Toute scène marquée `a_verifier` **bloque** la publication.
5. **Voix** — edge-tts, voix `fr-FR-HenriNeural`. La durée réelle de la
   voix fixe la durée de chaque scène (synchronisation automatique).
6. **Visuels** — Pixabay **en priorité** (les nouvelles clés Pexels sont
   en pause), Pexels en secours ; Freesound filtré CC0, Xeno-canto
   sans clé. Cache local + journal d'usage : un clip n'est pas
   réutilisé dans 2 vidéos proches.
7. **Animation** — Ken Burns (FFmpeg) : zoom avant/arrière, travelling.
8. **Mixage** — ducking sidechain (voix prioritaire), normalisation
   `loudnorm I=-16` (standard YouTube).
9. **Rendu** — assemblage FFmpeg 1080p, sous-titres SRT, miniature
   Pillow 1280×720 (3 mots max).
10. **Publication** — mode **relecture** par défaut : vidéo privée +
    planifiée sur YouTube (catégorie Animaux, langue fr, déclaration
    de contenu IA activée), 3 Shorts extraits, artefact archivé 3 jours
    pour validation humaine (5 min) dans YouTube Studio.

## Les secrets à mettre dans GitHub

*Settings → Secrets and variables → Actions* (ne jamais écrire une clé
dans le code, ni dans le chat) :

| Secret | À quoi il sert |
|---|---|
| `GEMINI_API_KEY` | Script JSON (`gemini-3.8-flash`) + fact-check |
| `YT_CLIENT_ID` | Publication YouTube (OAuth) |
| `YT_CLIENT_SECRET` | Publication YouTube (OAuth) |
| `YT_REFRESH_TOKEN` | Publication YouTube (OAuth) |
| `SHEET_ID` | ID du Google Sheet des 60 sujets |

**Gratuits et sans clé (utilisés d'office par le robot Varus) :**
Openverse (photos + sons), Wikimedia Commons (photos + vidéos),
Wikipédia/GBIF (faits). En secours seulement si leurs clés sont
présentes un jour : Pixabay, Pexels, Freesound, Xeno-canto.

## Lancer en local (mode test, sans clés)

```bash
pip install -r requirements.txt
python src/main.py --dry-run
```

Le dry-run récupère les faits du prochain sujet (Guépard), valide le
script d'exemple (`tests/sample_script.json`) et liste ensuite
**proprement les clés manquantes** — il n'appelle aucune API payante
et ne publie rien.

Pour produire une vidéo complète en local (clés requises) :

```bash
export GEMINI_API_KEY=...
export YT_CLIENT_ID=... YT_CLIENT_SECRET=... YT_REFRESH_TOKEN=...
python src/main.py --topic Guépard --no-upload   # sans publier
python src/main.py                               # run complet
```

FFmpeg doit être installé (`sudo apt-get install -y ffmpeg`).

## Structure du dépôt

```
.
├── .github/workflows/publish.yml   # cron 05:00 et 14:00 UTC
├── config/
│   ├── settings.yaml               # voix, 1080p, quotas
│   ├── sfx_library.json            # sons validés (clés autorisées)
│   ├── ambiances.json
│   └── prompts/                    # prompt maître + fact-check
├── src/                            # main.py, topics, facts, script_gen,
│                                   # fact_check, voice, assets, animate,
│                                   # mix, render, thumbnail, shorts, upload
├── tests/sample_script.json        # script d'exemple (dry-run)
├── topics.csv                      # banque de 60 sujets (copie)
├── cache/                          # clips et sons (ignoré par git)
└── output/                         # vidéos finales (ignoré par git)
```

## Ce qui reste à faire avant le premier vrai lancement

1. **Créer les clés** (Gemini, Groq, Pixabay, Freesound, YouTube OAuth)
   et les mettre dans les secrets GitHub.
2. **Créer le dépôt GitHub** et y pousser ce dossier.
3. **Importer `topics.csv` dans Google Sheets** et noter son `SHEET_ID`
   dans les secrets (sans lui, le pipeline utilise le CSV local).
4. **Demander l'audit de conformité de l'API YouTube** : sans audit,
   les vidéos sont forcées en privé — il faut donc valider en mode
   relecture dans YouTube Studio.
5. **Prototype** : faire tourner le pipeline à la main sur les 3
   premiers sujets (Guépard, Orque, Loup gris), relire, ajuster le
   prompt maître et valider la bibliothèque de sons à l'oreille.

## Limites connues

- Les clips IA (`clip_ia`, max 20 %) et la parallaxe 2.5D demandent
  un GPU : ils se génèrent sur Google Colab/Kaggle, pas sur le runner
  GitHub Actions (repli automatique sur un zoom avant).
- Les titres Wikipédia FR de `topics.csv` n'ont pas tous été vérifiés
  un par un : à confirmer pendant le prototype (semaine 2).
- YouTube peut démonétiser le contenu répétitif produit en masse :
  la relecture humaine de 5 minutes avant publication est obligatoire.
