"""Fact-check du script avec Gemini (gemini-3.8-flash).

Le robot Varus n'a plus besoin de Groq : la même clé Gemini qui écrit
le script vérifie aussi les faits. Un seul petit appel : on compare
le script aux sources factuelles.
Verdict : "ok" ou "a_corriger" + liste des problèmes par scène.
Les scènes problématiques sont marquées "a_verifier": true dans le script.
"""

import json
import os

import requests

MODELE = "gemini-3.8-flash"
URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
       f"{MODELE}:generateContent")


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
    r = requests.post(
        URL,
        params={"key": cle},
        headers={"Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.0,
                                   "responseMimeType": "application/json"}},
        timeout=120)
    r.raise_for_status()
    contenu = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    resultat = json.loads(contenu)
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
