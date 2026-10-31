"""Reprendre Varus : reprendre EXACTEMENT là où ça s'est arrêté.

Règle de Sam : rien ne se perd, le temps est précieux.
- Un script déjà préparé n'est JAMAIS régénéré.
- Un segment déjà monté et valide n'est JAMAIS refait.
- Un shard complet n'est JAMAIS relancé.
- Seul le travail manquant ou invalide est refait, puis on assemble
  et on livre la vraie vidéo.

État lu dans output/ (le dossier que les artefacts GitHub
reconstituent) : script.json, sujet.json, shard-*/manifest.json.
"""

import json
import os


def _manifest_valide(dossier_shard):
    """Un shard est terminé si son manifest existe et que chaque
    segment et chaque voix listés existent et ne sont pas vides."""
    chemin = os.path.join(dossier_shard, "manifest.json")
    if not os.path.exists(chemin):
        return False, []
    try:
        with open(chemin, encoding="utf-8") as f:
            man = json.load(f)
    except Exception:
        return False, []
    scenes = man.get("scenes", [])
    if not scenes:
        return False, []
    for entree in scenes:
        for cle in ("segment", "voice_path"):
            chemin_f = entree.get(cle)
            if not chemin_f or not os.path.exists(chemin_f):
                return False, []
            if os.path.getsize(chemin_f) == 0:
                return False, []
    return True, scenes


def etat_reprise(dossier_sortie, shard_count=10):
    """Renvoie l'état de reprise : ce qui est déjà fait, ce qui manque.

    {
      "script": bool, "sujet": bool,
      "shards_termines": [indices], "shards_manquants": [indices],
      "scenes_faites": [ids],
    }
    """
    script_ok = os.path.exists(os.path.join(dossier_sortie, "script.json"))
    sujet_ok = os.path.exists(os.path.join(dossier_sortie, "sujet.json"))
    termines, manquants, scenes_faites = [], [], []
    for i in range(shard_count):
        dossier = os.path.join(dossier_sortie, f"shard-{i}")
        ok, scenes = _manifest_valide(dossier)
        if ok:
            termines.append(i)
            scenes_faites.extend(s["id"] for s in scenes)
        else:
            manquants.append(i)
    return {
        "script": script_ok,
        "sujet": sujet_ok,
        "shards_termines": termines,
        "shards_manquants": manquants,
        "scenes_faites": sorted(scenes_faites),
    }


def charger_manifest_shard(dossier_sortie, shard_index):
    """Manifest existant d'un shard, ou None. Utilisé par
    stage_segments pour ne refaire que les scènes manquantes."""
    chemin = os.path.join(dossier_sortie, f"shard-{shard_index}",
                          "manifest.json")
    if not os.path.exists(chemin):
        return None
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def dossiers_shards_valides(dossier_sortie):
    """Dossiers shard-* qui contiennent VRAIMENT un manifest.

    Correction de l'échec assemble en 1 seconde du run #4 : l'ancien
    code prenait TOUS les dossiers shard-* et plantait avec une
    erreur non lisible (FileNotFound, sans code garde-fou) dès qu'un
    dossier n'avait pas de manifest. Maintenant, seuls les dossiers
    avec manifest sont pris ; les autres sont signalés, pas fatals.
    """
    valides, ignores = [], []
    if not os.path.isdir(dossier_sortie):
        return valides, ignores
    for d in sorted(os.listdir(dossier_sortie)):
        if not d.startswith("shard-"):
            continue
        chemin = os.path.join(dossier_sortie, d)
        if (os.path.isdir(chemin)
                and os.path.exists(os.path.join(chemin, "manifest.json"))):
            valides.append(chemin)
        else:
            ignores.append(chemin)
    return valides, ignores
