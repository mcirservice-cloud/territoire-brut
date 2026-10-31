"""Chemin BÉTA Varus : une vidéo à la fin, obligatoirement.

Règle décidée avec Sam : ALPHA ou BÉTA, il y a une vidéo à la fin.
Le BÉTA ne crée AUCUNE affirmation nouvelle : il découpe les faits
(Wikipédia / GBIF, déjà récupérés) en phrases, chaque phrase devient
une scène, narration = la phrase elle-même. Il n'y a donc rien à
fact-checker : aucune invention à vérifier, aucune scène à retirer
pour une affirmation non sourcée.

Le BÉTA est instantané (aucun appel LLM) : il sert quand l'ALPHA
échoue, dépasse son délai, ou laisse moins de 20 scènes après
fact-check. Dans ce dernier cas, on complète l'ALPHA avec des scènes
BÉTA jusqu'à 24 scènes, on renumérote, on alterne les animations.
"""

import re

ANIMATIONS = [
    "ken_burns_zoom_in",
    "ken_burns_zoom_out",
    "travelling_gauche",
    "travelling_droite",
    "parallaxe",
]

NB_SCENES_CIBLE = 24


def _phrases(faits):
    """Découpe les faits en phrases propres, 40 à 300 caractères."""
    texte = re.sub(r"\s+", " ", faits or "").strip()
    brutes = re.split(r"(?<=[.!?])\s+", texte)
    phrases, vues = [], set()
    for p in brutes:
        p = p.strip()
        if 40 <= len(p) <= 300 and p not in vues:
            phrases.append(p)
            vues.add(p)
    # Si trop peu de phrases longues, on coupe les longues phrases
    # sur les virgules / points-virgules pour atteindre le minimum.
    if len(phrases) < NB_SCENES_CIBLE:
        for p in brutes:
            for morceau in re.split(r"[,;]\s+", p.strip()):
                morceau = morceau.strip()
                if 40 <= len(morceau) <= 300 and morceau not in vues:
                    phrases.append(morceau)
                    vues.add(morceau)
                if len(phrases) >= NB_SCENES_CIBLE:
                    break
            if len(phrases) >= NB_SCENES_CIBLE:
                break
    return phrases


def construire_script_secours(faits, sujet, nb_scenes=NB_SCENES_CIBLE):
    """Script BÉTA complet, valide, à partir des faits seuls."""
    nom = sujet.get("nom", "Animal")
    nom_latin = sujet.get("nom_latin") or nom
    phrases = _phrases(faits)[:nb_scenes]
    scenes = []
    for i, phrase in enumerate(phrases, start=1):
        scenes.append({
            "id": i,
            "narration": phrase,
            "visuel": {
                "requete_pexels_en": f"{nom_latin} wildlife nature",
                "requete_alternative_en": "savanna animal",
            },
            "duree_estimee_s": 8,
            "animation": ANIMATIONS[(i - 1) % len(ANIMATIONS)],
            "musique": "calme",
            "ambiance": None,
            "sfx": [],
        })
    return {
        "meta": {
            "titre": f"{nom} : {sujet.get('angle', 'survie')}",
            "description": (faits or "")[:800],
            "miniature": {"texte": nom.upper(), "emotion": "danger"},
            "sources": ["Wikipedia"],
            "chemin": "beta",
        },
        "scenes": scenes,
    }


def completer_script(script, faits, sujet, cible=NB_SCENES_CIBLE):
    """Complète un script ALPHA amputé avec des scènes BÉTA.

    Garde les scènes ALPHA non signalées, ajoute des scènes BÉTA
    (faits directs) jusqu'à la cible, renumérote, alterne les
    animations. Renvoie le script complété.
    """
    secours = construire_script_secours(faits, sujet, nb_scenes=cible)
    gardees = [s for s in script.get("scenes", [])
               if not s.get("a_verifier")]
    for s in gardees:
        s.pop("a_verifier", None)
    scenes = list(gardees)
    for s in secours["scenes"]:
        if len(scenes) >= cible:
            break
        scenes.append(dict(s))
    for i, s in enumerate(scenes, start=1):
        s["id"] = i
        s["animation"] = ANIMATIONS[(i - 1) % len(ANIMATIONS)]
    script["scenes"] = scenes
    script.setdefault("meta", secours["meta"])
    script["meta"]["chemin"] = (
        "alpha+beta" if gardees else "beta")
    return script
