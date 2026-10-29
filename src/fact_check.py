"""Fact-check du script avec la chaîne LLM (Gemini puis secours).

Le robot Varus n'a plus besoin de Groq : la même clé Gemini qui écrit
le script vérifie aussi les faits, avec DeepSeek et OpenRouter en secours
(voir src/llm_chain.py). Un seul petit appel : on compare le script aux
sources factuelles.
Verdict : "ok" ou "a_corriger" + liste des problèmes par scène.
Les scènes problématiques sont marquées "a_verifier": true dans le script.
"""

import json
import os

from llm_chain import appeler_chaine, charger_json

MODELE = "gemini-3.8-flash"  # conservé pour compatibilité (1er de la chaîne)


def fact_check(script, sources, modele_prompt):
    """Renvoie {"problemes": [...], "verdict": "ok" | "a_corriger"}."""
    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        raise RuntimeError(
            "Clé Gemini manquante : définissez la variable d'environnement "
            "GEMINI_API_KEY (ou le secret GitHub GEMINI_API_KEY).")
    prompt = (modele_prompt
              .replace("{{SOURCES}}", sources)
              .replace("{{SCRIPT}}", json.dumps(script, ensure_ascii=False)))
    contenu = appeler_chaine(prompt, 0.0, cle)
    resultat = charger_json(contenu)
    if resultat.get("verdict") not in ("ok", "a_corriger"):
        resultat["verdict"] = "a_corriger" if resultat.get("problemes") else "ok"
    return resultat


def appliquer_fact_check(script, resultat):
    """Marque a_verifier=true les scènes citées. Renvoie le nb de problèmes."""
    problemes = resultat.get("problemes", [])
    par_scene = {p.get("scene_id") for p in problemes}
    for scene in script.get("scenes", []):
        if scene.get("id") in par_scene:
            scene["a_verifier"] = True
    return len(problemes)


def corriger_scenes(script, problemes, faits):
    """Demande à la chaîne LLM de réécrire la narration des scènes
    signalées, sans toucher au reste de la structure.

    Renvoie la liste « corrections » du LLM :
    [{"scene_id": int, "narration": str,
      "requete_pexels_en": str, "requete_alternative_en": str}].
    Seule la narration (et, si fournie, les requêtes visuelles) change :
    ids, durées, animations, sfx et ambiances restent intacts, donc le
    script corrigé reste structurellement valide."""
    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        raise RuntimeError("Clé Gemini manquante pour la correction.")
    scenes_signalees = [s for s in script.get("scenes", [])
                        if s.get("id") in {p.get("scene_id")
                                           for p in problemes}]
    prompt = (
        "Tu es correcteur de script documentaire. Réécris UNIQUEMENT la "
        "narration des scènes signalées ci-dessous, pour supprimer chaque "
        "affirmation non soutenue par les sources : soit tu la corriges "
        "avec un fait présent dans les sources, soit tu la retires sans "
        "inventer de nouveau chiffre, record, comportement ou date. "
        "Ne change rien d'autre : ni les ids, ni les durées, ni les "
        "animations, ni les sfx. Si la narration corrigée change le sujet "
        "visuel, adapte les deux requêtes visuelles anglaises en "
        "conséquence, sinon reprends-les telles quelles.\n"
        "Réponds en JSON strict : "
        '{"corrections": [{"scene_id": 0, "narration": "", '
        '"requete_pexels_en": "", "requete_alternative_en": ""}]}.\n'
        f"FAITS (sources) : {faits}\n"
        f"PROBLÈMES : {json.dumps(problemes, ensure_ascii=False)}\n"
        f"SCÈNES SIGNALÉES : "
        f"{json.dumps(scenes_signalees, ensure_ascii=False)}")
    contenu = appeler_chaine(prompt, 0.0, cle)
    resultat = charger_json(contenu)
    corrections = resultat.get("corrections", [])
    if not isinstance(corrections, list) or not corrections:
        raise RuntimeError("Correction LLM vide ou illisible.")
    return corrections


def appliquer_corrections(script, corrections):
    """Applique les corrections à leurs scènes. Renvoie le nb appliqué.

    Une correction n'est appliquée que si son scene_id existe et que sa
    narration est non vide ; les requêtes visuelles ne sont remplacées
    que si elles sont fournies et non vides. Les scènes corrigées voient
    leur marque a_verifier effacée, en attendant la revérification."""
    par_id = {s.get("id"): s for s in script.get("scenes", [])}
    appliquees = 0
    for correction in corrections:
        scene = par_id.get(correction.get("scene_id"))
        narration = (correction.get("narration") or "").strip()
        if scene is None or not narration:
            continue
        scene["narration"] = narration
        visuel = scene.setdefault("visuel", {})
        for champ in ("requete_pexels_en", "requete_alternative_en"):
            valeur = (correction.get(champ) or "").strip()
            if valeur:
                visuel[champ] = valeur
        scene["a_verifier"] = False
        appliquees += 1
    return appliquees
