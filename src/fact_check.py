"""Fact-check du script avec Groq (llama-3.1-8b-instant).

Un seul petit appel : on compare le script aux sources factuelles.
Verdict : "ok" ou "a_corriger" + liste des problèmes par scène.
Les scènes problématiques sont marquées "a_verifier": true dans le script.
"""

import json
import os

import requests

MODELE = "llama-3.1-8b-instant"
URL = "https://api.groq.com/openai/v1/chat/completions"


def fact_check(script, sources, modele_prompt):
    """Renvoie {"problemes": [...], "verdict": "ok" | "a_corriger"}."""
    cle = os.environ.get("GROQ_API_KEY")
    if not cle:
        raise RuntimeError(
            "Clé Groq manquante : définissez la variable d'environnement "
            "GROQ_API_KEY (ou le secret GitHub GROQ_API_KEY).")
    prompt = (modele_prompt
              .replace("{{SOURCES}}", sources)
              .replace("{{SCRIPT}}", json.dumps(script, ensure_ascii=False)))
    r = requests.post(
        URL,
        headers={"Authorization": f"Bearer {cle}",
                 "Content-Type": "application/json"},
        json={"model": MODELE,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.0,
              "response_format": {"type": "json_object"}},
        timeout=120)
    r.raise_for_status()
    contenu = r.json()["choices"][0]["message"]["content"]
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
