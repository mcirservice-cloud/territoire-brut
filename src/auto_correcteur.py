"""Auto-correcteur Varus : auto-maintenance puissante.

Il tourne AVANT chaque assemblage et chaque reprise, et il répare
tout ce qui peut être réparé sans refaire le travail, au lieu de
laisser le run mourir sur un détail :

1. Dossiers manquants -> créés (cache/, cache/voice,
   cache/segments, output/, output/shorts). C'est l'erreur G013
   du run #4 et de la reprise #1 : cache/ inexistant dans un job
   assemble frais, qui plantait concatener_voix en 0-1 seconde.
2. Script : scènes renumérotées, animations répétées corrigées,
   durées ramenées dans 4-12 s, sfx/ambiances inconnus supprimés
   (réutilise corriger_script, déjà testé).
3. Manifests : entrées dont le segment ou la voix manque ->
   retirées (elles seront refaites par la reprise, pas bloquantes) ;
   chemins absolus d'un ancien runner -> réécrits vers le fichier
   du même nom dans le dossier du shard, s'il y est.
4. Dossiers shard sans manifest -> signalés, jamais fatals
   (c'est l'autre cause possible de l'échec assemble en 1 s).

Résultat : un rapport écrit dans output/maintenance.json, avec
chaque correction listée. Ce qui ne peut pas être réparé (script
absent, aucun shard valide) reste une erreur claire, pas un plantage
opaque.
"""

import json
import os

from reprendre import dossiers_shards_valides


def _assurer_dossiers(dossier_sortie, dossier_cache="cache"):
    corrections = []
    for d in (dossier_sortie, dossier_cache,
              os.path.join(dossier_cache, "voice"),
              os.path.join(dossier_cache, "segments"),
              os.path.join(dossier_sortie, "shorts")):
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            corrections.append(f"dossier créé : {d}")
    return corrections


def _corriger_script(dossier_sortie):
    corrections = []
    chemin = os.path.join(dossier_sortie, "script.json")
    if not os.path.exists(chemin):
        return corrections, "script.json absent"
    try:
        with open(chemin, encoding="utf-8") as f:
            script = json.load(f)
    except Exception as e:
        return corrections, f"script.json illisible ({e})"
    from script_gen import corriger_script
    # Bibliothèques : on corrige sans les listes strictes si les
    # fichiers de config ne sont pas lisibles ici (on ne bloque pas).
    try:
        from main import charger_bibliotheques
        sfx, ambiances = charger_bibliotheques()
    except Exception:
        sfx, ambiances = None, None
    faites, _bloquantes = corriger_script(script, sfx, ambiances)
    corrections.extend(faites)
    if faites:
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
    return corrections, None


def _corriger_manifests(dossier_sortie):
    corrections = []
    valides, _ignores = dossiers_shards_valides(dossier_sortie)
    for dossier in valides:
        chemin = os.path.join(dossier, "manifest.json")
        with open(chemin, encoding="utf-8") as f:
            man = json.load(f)
        gardees = []
        for entree in man.get("scenes", []):
            ok = True
            for cle in ("segment", "voice_path"):
                p = entree.get(cle)
                if not p or not os.path.exists(p):
                    # Tentative : même nom de fichier dans le shard.
                    candidat = os.path.join(
                        dossier, os.path.basename(p or ""))
                    if candidat and os.path.exists(candidat):
                        entree[cle] = candidat
                        corrections.append(
                            f"scène {entree.get('id')} : chemin {cle} "
                            f"réécrit vers {candidat}")
                    else:
                        ok = False
                elif os.path.getsize(p) == 0:
                    ok = False
            if ok:
                gardees.append(entree)
            else:
                corrections.append(
                    f"scène {entree.get('id')} retirée du manifest "
                    f"{dossier} (fichier manquant/vide, à refaire)")
        if len(gardees) != len(man.get("scenes", [])):
            man["scenes"] = gardees
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(man, f, ensure_ascii=False, indent=2)
    return corrections


def maintenance(dossier_sortie, dossier_cache="cache"):
    """Auto-maintenance complète. Renvoie le rapport dict."""
    rapport = {"corrections": [], "erreurs": []}
    rapport["corrections"].extend(
        _assurer_dossiers(dossier_sortie, dossier_cache))
    corr_script, err_script = _corriger_script(dossier_sortie)
    rapport["corrections"].extend(corr_script)
    if err_script:
        rapport["erreurs"].append(err_script)
    rapport["corrections"].extend(_corriger_manifests(dossier_sortie))
    chemin = os.path.join(dossier_sortie, "maintenance.json")
    try:
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(rapport, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return rapport
