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
