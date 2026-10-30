"""Génération du script JSON avec la chaîne LLM (OpenRouter gratuit d'abord).

Ordre des fournisseurs (voir src/llm_chain.py) : OpenRouter gratuit
(modèle configuré puis routeur auto), DeepSeek, puis Gemini REST borné
en dernier recours uniquement. Les couches sans clé sont ignorées,
jamais bloquantes. AUCUN appel SDK : tous les appels passent par HTTP
avec timeout explicite, donc la génération ne peut plus pendre
indéfiniment comme au run n°9.

Validation structurelle avant acceptation :
- ids de scènes continus (1..N)
- max 20 % de scènes en clip_ia
- jamais deux animations identiques consécutives
- clés sfx / ambiances uniquement dans les bibliothèques autorisées
- durées de scène entre 4 et 12 secondes
Correction auto (run #10) : ce qui est corrigeable est corrigé au lieu
de rejeter tout le script — animation répétée changée, sfx inconnu
supprimé ou déplacé vers ambiance si c'est une ambiance connue
(ex. desert_vent), durée ramenée à la borne, clip_ia en trop converti.
Seuls restent bloquants : JSON invalide, aucune scène, meta.titre manquant.
3 essais maximum, puis échec propre.
"""

import json
import os

from llm_chain import (appeler_generation, au_moins_une_cle, charger_json,
                       config_chaine)

TEMPERATURE = 0.7
ESSAIS_MAX = 3
PART_MAX_CLIP_IA = 0.2


class ScriptInvalide(Exception):
    """Le script JSON ne respecte pas les règles de validation."""


def charger_bibliotheques(dossier_config="config"):
    """Renvoie (cles_sfx, cles_ambiances) depuis les JSON de config."""
    with open(os.path.join(dossier_config, "sfx_library.json"), encoding="utf-8") as f:
        sfx = set(json.load(f))
    with open(os.path.join(dossier_config, "ambiances.json"), encoding="utf-8") as f:
        ambiances = set(json.load(f))
    return sfx, ambiances


def validate(data, sfx_autorises=None, ambiances_autorisees=None,
             part_max_clip_ia=PART_MAX_CLIP_IA):
    """Valide la structure du script. Lève ScriptInvalide sinon."""
    if "scenes" not in data or not data["scenes"]:
        raise ScriptInvalide("aucune scène dans le script")
    scenes = data["scenes"]

    ids = [s["id"] for s in scenes]
    if ids != list(range(1, len(scenes) + 1)):
        raise ScriptInvalide(f"ids de scènes non continus : {ids}")

    nb_ia = sum(s.get("animation") == "clip_ia" for s in scenes)
    if nb_ia / len(scenes) > part_max_clip_ia:
        raise ScriptInvalide(
            f"trop de clips IA : {nb_ia}/{len(scenes)} "
            f"(maximum {part_max_clip_ia:.0%})")

    for a, b in zip(scenes, scenes[1:]):
        if a.get("animation") == b.get("animation"):
            raise ScriptInvalide(
                f"animation répétée entre les scènes {a['id']} et {b['id']} "
                f"({a.get('animation')})")

    for s in scenes:
        duree = s.get("duree_estimee_s", 0)
        if not 4 <= duree <= 12:
            raise ScriptInvalide(
                f"scène {s['id']} : durée {duree}s hors de la plage 4-12 s")
        if sfx_autorises is not None:
            for sfx in s.get("sfx", []):
                if sfx.get("cle") not in sfx_autorises:
                    raise ScriptInvalide(
                        f"scène {s['id']} : clé sfx inconnue « {sfx.get('cle')} »")
        if ambiances_autorisees is not None and s.get("ambiance"):
            if s["ambiance"] not in ambiances_autorisees:
                raise ScriptInvalide(
                    f"scène {s['id']} : ambiance inconnue « {s.get('ambiance')} »")

    if "meta" not in data or "titre" not in data.get("meta", {}):
        raise ScriptInvalide("métadonnées (meta.titre) manquantes")
    return True


ANIMATIONS_CORRECTION = [
    "ken_burns_zoom_in",
    "ken_burns_zoom_out",
    "travelling_gauche",
    "travelling_droite",
    "parallaxe",
    "clip_ia",
]


def corriger_script(data, sfx_autorises=None, ambiances_autorisees=None):
    """Corrige ce qui est corrigeable, sans rejeter tout le script.

    Renvoie (corrections, erreurs_bloquantes). Les erreurs bloquantes
    restent : aucune scène, meta.titre manquant, trop de clip_ia même
    après conversion. Tout le reste est corrigé et journalisé.
    """
    corrections = []
    erreurs = []
    scenes = data.get("scenes") if isinstance(data, dict) else None
    if not scenes:
        return corrections, ["aucune scène dans le script"]
    if "meta" not in data or "titre" not in data.get("meta", {}):
        erreurs.append("métadonnées (meta.titre) manquantes")

    for i, s in enumerate(scenes, start=1):
        if s.get("id") != i:
            corrections.append(f"scène renumérotée en {i} (était {s.get('id')})")
            s["id"] = i

    nb_ia = sum(s.get("animation") == "clip_ia" for s in scenes)
    max_ia = int(len(scenes) * 0.2)
    if nb_ia > max_ia:
        reste = nb_ia - max_ia
        for s in scenes:
            if reste <= 0:
                break
            if s.get("animation") == "clip_ia" and s.get("phase") not in ("accroche", "climax"):
                s["animation"] = "ken_burns_zoom_in"
                reste -= 1
                corrections.append(f"scène {s['id']} : clip_ia en trop -> ken_burns_zoom_in")
        if reste > 0:
            erreurs.append(f"trop de clips IA même après correction : {nb_ia}/{len(scenes)}")

    precedent = None
    for s in scenes:
        anim = s.get("animation")
        if anim == precedent:
            nouveau = next((a for a in ANIMATIONS_CORRECTION if a != precedent and a != "clip_ia"), "ken_burns_zoom_out")
            corrections.append(f"scène {s['id']} : animation répétée {anim} -> {nouveau}")
            s["animation"] = nouveau
            anim = nouveau
        precedent = anim

        duree = s.get("duree_estimee_s", 0)
        if duree < 4:
            s["duree_estimee_s"] = 4
            corrections.append(f"scène {s['id']} : durée {duree}s -> 4s")
        elif duree > 12:
            s["duree_estimee_s"] = 12
            corrections.append(f"scène {s['id']} : durée {duree}s -> 12s")

        if sfx_autorises is not None:
            gardes = []
            for sfx in s.get("sfx", []):
                cle = sfx.get("cle")
                if cle in sfx_autorises:
                    gardes.append(sfx)
                elif ambiances_autorisees is not None and cle in ambiances_autorisees and not s.get("ambiance"):
                    s["ambiance"] = cle
                    corrections.append(f"scène {s['id']} : « {cle} » déplacé de sfx vers ambiance")
                else:
                    corrections.append(f"scène {s['id']} : sfx inconnu « {cle} » supprimé")
            s["sfx"] = gardes

        if ambiances_autorisees is not None and s.get("ambiance"):
            if s["ambiance"] not in ambiances_autorisees:
                corrections.append(f"scène {s['id']} : ambiance inconnue « {s['ambiance']} » supprimée")
                s["ambiance"] = None

    return corrections, erreurs


def construire_prompt(modele_prompt, sujet, faits, sfx, ambiances):
    """Remplit les {{variables}} du prompt maître."""
    remplacements = {
        "{{SUJET}}": sujet["nom"],
        "{{DUREE_MIN}}": str(sujet["duree"]),
        "{{ANGLE}}": sujet["angle"],
        "{{TENSION_GLOBALE}}": str(sujet["tension"]),
        "{{FAITS_WIKIPEDIA_GBIF}}": faits,
        "{{LISTE_SFX_VALIDES}}": ", ".join(sorted(sfx)),
        "{{LISTE_AMBIANCES}}": ", ".join(sorted(ambiances)),
    }
    prompt = modele_prompt
    for cle, valeur in remplacements.items():
        prompt = prompt.replace(cle, valeur)
    return prompt


def _generer_texte(prompt_courant, config=None):
    """Génère le texte brut via la chaîne OpenRouter-first.

    Renvoie le texte brut de la réponse. Lève l'erreur du dernier
    fournisseur si tous échouent ; en l'absence totale de clé,
    generate_script a déjà levé une erreur claire avant cet appel.
    """
    return appeler_generation(prompt_courant, TEMPERATURE, config)


def generate_script(prompt, sfx_autorises=None, ambiances_autorisees=None,
                    essais=ESSAIS_MAX, config=None):
    """Appelle la chaîne LLM en mode JSON jusqu'à obtenir un script valide."""
    if not au_moins_une_cle():
        raise RuntimeError(
            "Aucune clé LLM définie : définissez au moins une de "
            "OPENROUTER_API_KEY, DEEPSEEK_API_KEY ou GEMINI_API_KEY "
            "(secrets GitHub, jamais dans le chat).")
    config = config or config_chaine()

    prompt_courant = prompt
    derniere_erreur = None
    for essai in range(1, essais + 1):
        texte = _generer_texte(prompt_courant, config)
        data = None
        try:
            data = charger_json(texte)
            validate(data, sfx_autorises, ambiances_autorisees)
            return data
        except (ValueError, ScriptInvalide, TypeError) as e:
            derniere_erreur = e
            print(f"[script] Essai {essai}/{essais} invalide : {e}")
            # Correction auto : si le JSON a été lu, on corrige ce qui
            # est corrigeable et on revérifie avant de gaspiller un essai.
            if data is not None:
                corrections, bloquantes = corriger_script(
                    data, sfx_autorises, ambiances_autorisees)
                if corrections:
                    print(f"[script] Correction auto : {'; '.join(corrections)}")
                if not bloquantes:
                    try:
                        validate(data, sfx_autorises, ambiances_autorisees)
                        print(f"[script] Script corrigé et accepté à l'essai {essai}/{essais}.")
                        return data
                    except (ValueError, ScriptInvalide) as e2:
                        derniere_erreur = e2
                        print(f"[script] Après correction, toujours invalide : {e2}")
                else:
                    derniere_erreur = "; ".join(bloquantes)
            prompt_courant = prompt + (
                f"\n\nERREUR PRÉCÉDENTE : {derniere_erreur}. "
                "Corrige et renvoie un JSON valide.")
    raise RuntimeError(
        f"Script invalide après {essais} essais "
        f"(dernière erreur : {derniere_erreur}).")
