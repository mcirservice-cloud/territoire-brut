"""Fact-check du script avec la réflexion LLM (DeepSeek d'abord, secours).

Le robot Varus n'a plus besoin de Groq : la réflexion critique
(relecture du script, détection d'incohérences) passe par
appeler_reflexion() — DeepSeek quand DEEPSEEK_API_KEY est définie,
Gemini puis OpenRouter gratuit en secours (voir src/llm_chain.py).
Un seul petit appel : on compare le script aux sources factuelles.
Verdict : "ok" ou "a_corriger" + liste des problèmes par scène.
Les scènes problématiques sont marquées "a_verifier": true dans le script.

La boucle de correction est BORNÉE (corriger_et_reverifier, max 4
tentatives par défaut, voir run #11) : le fact-check ne converge pas
toujours — chaque correction peut faire apparaître une nouvelle scène
signalée (run #11 : 6 problèmes initiaux, puis 5, puis 1 scène neuve,
la 63, « Il l'étrangle rapidement. »). Après les tentatives, les
scènes encore signalées sont donc RETIRÉES du script par
retirer_scenes_signalees (décision Sam 2026-10-29 : auto-ajuster pour
ne plus bloquer, poursuivre le traitement) — jamais publiées avec
avertissement, jamais réécrites avec une nouvelle affirmation inventée :
l'affirmation signalée disparaît avec sa scène. Sauvegarde : si le
retrait laisserait moins de 20 scènes, on bloque toujours mais on
sauvegarde tout, comme avant.
"""

import json
import os

from llm_chain import appeler_reflexion, au_moins_une_cle, charger_json

MODELE = "gemini-3.8-flash"  # conservé pour compatibilité (2e de la réflexion)
CORRECTION_MAX_TENTATIVES = 4  # augmenté après run #11 (2 insuffisants, non-convergence)
ANIMATIONS = [
    "ken_burns_zoom_in",
    "ken_burns_zoom_out",
    "travelling_gauche",
    "travelling_droite",
    "parallaxe",
    "clip_ia",
]


def _exiger_une_cle(usage):
    if not au_moins_une_cle():
        raise RuntimeError(
            f"Aucune clé LLM pour {usage} : définissez au moins une de "
            "GEMINI_API_KEY, DEEPSEEK_API_KEY ou OPENROUTER_API_KEY.")


def fact_check(script, sources, modele_prompt):
    """Renvoie {"problemes": [...], "verdict": "ok" | "a_corriger"}."""
    _exiger_une_cle("le fact-check")
    prompt = (modele_prompt
              .replace("{{SOURCES}}", sources)
              .replace("{{SCRIPT}}", json.dumps(script, ensure_ascii=False)))
    contenu = appeler_reflexion(prompt, 0.0)
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
    _exiger_une_cle("la correction")
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
    contenu = appeler_reflexion(prompt, 0.0)
    resultat = charger_json(contenu)
    corrections = resultat.get("corrections", [])
    if not isinstance(corrections, list) or not corrections:
        raise RuntimeError("Correction LLM vide ou illisible.")
    return corrections


def corriger_et_reverifier(script, faits, prompt_check, resultat_initial,
                           max_tentatives=CORRECTION_MAX_TENTATIVES):
    """Boucle de correction BORNÉE à partir d'un premier fact-check déjà
    appliqué (scènes marquées a_verifier).

    Chaque tentative : réécriture des scènes signalées, puis
    revérification complète. Arrêt dès qu'aucune scène n'est plus
    signalée, ou après max_tentatives tentatives.

    Renvoie (scenes_restantes, journal) où scenes_restantes est la liste
    des ids encore signalés (vide = le script peut continuer) et journal
    la trace de chaque tentative (scènes, corrections, problèmes, erreur
    éventuelle). Ne lève JAMAIS : en cas d'échec de la correction, la
    tentative est journalisée et la boucle s'arrête — c'est l'appelant
    qui applique la politique « Bloquer mais tout sauvegarder » avec
    les scènes restantes.
    """
    resultat = resultat_initial
    journal = []
    for tentative in range(1, max_tentatives + 1):
        scenes = [s["id"] for s in script.get("scenes", [])
                  if s.get("a_verifier")]
        if not scenes:
            break
        try:
            corrections = corriger_scenes(
                script, resultat.get("problemes", []), faits)
            nb_corrigees = appliquer_corrections(script, corrections)
            resultat = fact_check(script, faits, prompt_check)
            nb_problemes = appliquer_fact_check(script, resultat)
            journal.append({
                "tentative": tentative,
                "scenes_signalees": scenes,
                "corrections_appliquees": nb_corrigees,
                "problemes_apres": nb_problemes,
                "problemes": resultat.get("problemes", []),
                "verdict": resultat.get("verdict"),
            })
            if nb_problemes == 0:
                break
        except Exception as e:
            journal.append({
                "tentative": tentative,
                "scenes_signalees": scenes,
                "erreur": f"{type(e).__name__}: {e}",
            })
            break
    scenes_restantes = [s["id"] for s in script.get("scenes", [])
                        if s.get("a_verifier")]
    return scenes_restantes, journal


def retirer_scenes_signalees(script, ids_a_retirer):
    """Auto-ajustement final (run #11) : retire les scènes encore
    signalées après les tentatives de correction, puis renumérote et
    répare les animations répétées créées par le retrait.

    Renvoie la liste des ids retirés (anciens ids). Ne retire rien si
    le script restant aurait moins de 20 scènes : l'appelant doit
    alors bloquer comme avant. N'invente aucune affirmation : la scène
    signalée disparaît entièrement, avec sa narration.
    """
    ids = set(ids_a_retirer or [])
    if not ids:
        return []
    restantes = [s for s in script.get("scenes", [])
                 if s.get("id") not in ids]
    if len(restantes) < 20:
        return []
    retires = [s.get("id") for s in script.get("scenes", [])
               if s.get("id") in ids]
    script["scenes"] = restantes
    precedent = None
    for i, s in enumerate(restantes, start=1):
        s["id"] = i
        s.pop("a_verifier", None)
        anim = s.get("animation")
        if anim == precedent:
            nouveau = next((a for a in ANIMATIONS
                            if a != precedent and a != "clip_ia"),
                           "ken_burns_zoom_out")
            s["animation"] = nouveau
            anim = nouveau
        precedent = anim
    return retires


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
