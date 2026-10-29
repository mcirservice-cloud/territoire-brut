"""Génération du script JSON avec la chaîne LLM (Gemini puis secours).

Modèles essayés dans l'ordre (voir src/llm_chain.py) : Gemini 3.8-flash,
Gemini 3.5-flash, DeepSeek, OpenRouter GLM-5.3-Flash, OpenRouter gratuit.
Les couches sans clé sont ignorées, jamais bloquantes.

Validation structurelle avant acceptation :
- ids de scènes continus (1..N)
- max 20 % de scènes en clip_ia
- jamais deux animations identiques consécutives
- clés sfx / ambiances uniquement dans les bibliothèques autorisées
- durées de scène entre 4 et 12 secondes
3 essais maximum, puis échec propre.
"""

import json
import os
import time

from llm_chain import (AucunFournisseurSecours, appeler_fournisseurs_openai,
                       charger_json, config_chaine)

MODEL = "gemini-3.8-flash"
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


def _est_transitoire(e):
    """Vrai si l'erreur ressemble à une panne temporaire (surcharge 503/429
    ou coupure réseau) : ces cas méritent un réessai, pas un échec du run."""
    message = str(e)
    if any(code in message for code in
           ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
        return True
    nom = type(e).__name__
    if any(mot in nom for mot in
           ("RemoteProtocol", "Connect", "Timeout", "Network", "Connection")):
        return True
    if any(mot in message for mot in
           ("disconnected", "connection", "timeout", "timed out",
            "reset by peer", "EOF")):
        return True
    return isinstance(e, (ConnectionError, TimeoutError))


def _appeler_gemini(client, prompt_courant, modele=MODEL):
    """Appelle Gemini avec réessais sur les pannes temporaires
    (surcharge 503/429 ou coupure réseau) : une pointe de demande ou une
    connexion coupée ne doit pas tuer le run."""
    delais = [30, 60, 120]
    for tentative in range(1, len(delais) + 2):
        try:
            return client.models.generate_content(
                model=modele, contents=prompt_courant,
                config={"response_mime_type": "application/json",
                        "temperature": TEMPERATURE})
        except Exception as e:
            if not _est_transitoire(e) or tentative > len(delais):
                raise
            attente = delais[tentative - 1]
            print(f"[script] Gemini indisponible ({type(e).__name__}), "
                  f"nouvel essai dans {attente}s "
                  f"(tentative {tentative}/{len(delais) + 1})")
            time.sleep(attente)
    raise RuntimeError("Échec Gemini après réessais")


def _generer_texte(client, prompt_courant):
    """Essaie Gemini (SDK, modèles configurés) puis les secours OpenAI.
    Renvoie le texte brut de la réponse."""
    config = config_chaine()
    derniere_erreur = None
    for modele in config["gemini_modeles"]:
        try:
            return _appeler_gemini(client, prompt_courant, modele).text
        except Exception as e:
            derniere_erreur = e
            print(f"[script] {modele} indisponible, secours suivant...")
    try:
        return appeler_fournisseurs_openai(prompt_courant, TEMPERATURE, config)
    except AucunFournisseurSecours:
        raise derniere_erreur


def generate_script(prompt, sfx_autorises=None, ambiances_autorisees=None,
                    essais=ESSAIS_MAX):
    """Appelle la chaîne LLM en mode JSON jusqu'à obtenir un script valide."""
    from google import genai

    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        raise RuntimeError(
            "Clé Gemini manquante : définissez la variable d'environnement "
            "GEMINI_API_KEY (ou le secret GitHub GEMINI_API_KEY).")
    client = genai.Client(api_key=cle)

    prompt_courant = prompt
    derniere_erreur = None
    for essai in range(1, essais + 1):
        texte = _generer_texte(client, prompt_courant)
        try:
            data = charger_json(texte)
            validate(data, sfx_autorises, ambiances_autorisees)
            return data
        except (ValueError, ScriptInvalide) as e:
            # ValueError couvre JSONDecodeError + extraction JSON impossible :
            # une réponse non-JSON compte comme un essai invalide, pas un crash.
            derniere_erreur = e
            print(f"[script] Essai {essai}/{essais} invalide : {e}")
            prompt_courant = prompt + (
                f"\n\nERREUR PRÉCÉDENTE : {e}. "
                "Corrige et renvoie un JSON valide.")
    raise RuntimeError(
        f"Script invalide après {essais} essais "
        f"(dernière erreur : {derniere_erreur}).")
