"""Chaîne de secours LLM partagée par script_gen.py et fact_check.py.

Ordre des fournisseurs (le premier qui répond gagne) :
1. Gemini gemini-3.8-flash (clé GEMINI_API_KEY) — chemin actuel.
2. Gemini gemini-3.5-flash (même clé) — protège contre la surcharge d'un
   seul modèle (l'erreur 503 observée visait un modèle précis).
3. DeepSeek deepseek-chat (clé DEEPSEEK_API_KEY, endpoint OpenAI-compatible).
4. OpenRouter z-ai/glm-5.3-flash (clé OPENROUTER_API_KEY) — modèle payant
   mais très bon marché (prix officiel Z.ai : 0,15 $/M en entrée,
   0,50 $/M en sortie ; encore moins cher via OpenRouter selon l'hôte).
5. OpenRouter modèle gratuit configuré (clé OPENROUTER_API_KEY), puis le
   routeur automatique openrouter/free en tout dernier recours.

Règles :
- Une couche dont la clé est absente est ignorée avec une ligne de log,
  jamais une erreur bloquante (seule la clé Gemini primaire est exigée).
- Sur 404, 429, 503, 5xx ou erreur réseau, on passe au fournisseur suivant.
- Chaque appel réessaie les erreurs temporaires (30/60/120 s) avant
  d'abandonner ce fournisseur — la logique de réessai existante est conservée.
- Si tous les fournisseurs échouent, l'erreur du dernier est levée.
- Sortie JSON : response_mime_type côté Gemini, response_format
  json_object côté DeepSeek/OpenRouter ; si un fournisseur refuse le mode
  JSON, on réessaie sans puis on extrait le JSON du texte.

Aucune clé n'est écrite dans ce fichier : tout vient de l'environnement.
"""

import json
import os
import re
import time

import requests

DELAIS_REESSAI_S = [30, 60, 120]
GEMINI_MODELES = ["gemini-3.8-flash", "gemini-3.5-flash"]
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{modele}:generateContent")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODELE = "deepseek-chat"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELE_GLM = "z-ai/glm-5.3-flash"
OPENROUTER_MODELE_GRATUIT_DEFAUT = "google/gemma-4-31b-it:free"
OPENROUTER_SECOURS_AUTO = "openrouter/free"
TIMEOUT_S = 180


class AucunFournisseurSecours(Exception):
    """Aucune couche de secours n'est utilisable (clés absentes)."""


def _resume_erreur(erreur):
    """Première ligne de l'erreur, tronquée pour les logs."""
    return str(erreur).splitlines()[0][:160] if str(erreur) else "?"


def extraire_json(texte):
    """Extrait le premier objet JSON d'un texte (balises markdown tolérées)."""
    if not texte:
        raise ValueError("texte vide, aucun JSON à extraire")
    propre = re.sub(r"```(?:json)?", "", texte).strip()
    debut = propre.find("{")
    fin = propre.rfind("}")
    if debut < 0 or fin <= debut:
        raise ValueError("aucun objet JSON trouvé dans la réponse")
    return propre[debut:fin + 1]


def charger_json(texte):
    """json.loads, avec extraction tolérante en secours."""
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        return json.loads(extraire_json(texte))


def _charger_section_yaml():
    """Lit la section llm_chaine de config/settings.yaml ({} sinon)."""
    try:
        import yaml
    except ImportError:
        return {}
    chemins = ["config/settings.yaml",
               os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "config", "settings.yaml")]
    for chemin in chemins:
        try:
            with open(chemin, encoding="utf-8") as f:
                donnees = yaml.safe_load(f) or {}
            section = donnees.get("llm_chaine")
            return section if isinstance(section, dict) else {}
        except Exception:
            continue
    return {}


def config_chaine():
    """Configuration de la chaîne (settings.yaml, sinon valeurs par défaut)."""
    brutes = _charger_section_yaml()
    gratuits = brutes.get("openrouter_modeles_gratuits") or \
        [OPENROUTER_MODELE_GRATUIT_DEFAUT]
    if isinstance(gratuits, str):
        gratuits = [gratuits]
    secours_auto = brutes.get("openrouter_secours_auto",
                              OPENROUTER_SECOURS_AUTO)
    return {
        "gemini_modeles": brutes.get("gemini_modeles") or list(GEMINI_MODELES),
        "deepseek_modele": brutes.get("deepseek_modele") or DEEPSEEK_MODELE,
        "openrouter_modele_payant": brutes.get("openrouter_modele_payant") or
        OPENROUTER_MODELE_GLM,
        "openrouter_modeles_gratuits": gratuits,
        "openrouter_secours_auto": secours_auto or None,
        "delais_reessai_s": brutes.get("delais_reessai_s") or
        list(DELAIS_REESSAI_S),
    }


def _attendre(delais, tentative, modele, raison, journal):
    attente = delais[tentative - 1]
    journal(f"[llm] {modele} : {raison}, nouvel essai dans {attente}s "
            f"(tentative {tentative}/{len(delais) + 1})")
    time.sleep(attente)


def _appeler_gemini_rest(cle, modele, prompt, temperature, delais,
                         journal=print):
    """Gemini via REST, avec réessais sur erreurs temporaires. Renvoie le texte."""
    url = GEMINI_URL.format(modele=modele)
    for tentative in range(1, len(delais) + 2):
        try:
            r = requests.post(
                url,
                params={"key": cle},
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": temperature,
                                           "responseMimeType": "application/json"}},
                timeout=TIMEOUT_S)
        except requests.RequestException as e:
            if tentative <= len(delais):
                _attendre(delais, tentative, modele, f"erreur réseau ({e})",
                          journal)
                continue
            raise RuntimeError(f"{modele} : erreur réseau ({e})")
        if r.status_code == 200:
            try:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError, ValueError) as e:
                raise RuntimeError(f"{modele} : réponse inattendue ({e})")
        if r.status_code in (429, 500, 502, 503, 504) and tentative <= len(delais):
            _attendre(delais, tentative, modele, f"HTTP {r.status_code}", journal)
            continue
        raise RuntimeError(f"{modele} : HTTP {r.status_code} ({r.text[:200]})")
    raise RuntimeError(f"{modele} : échec après réessais")


def _appeler_openai_compatible(url, cle, modele, prompt, temperature, delais,
                               headers_extra=None):
    """Endpoint OpenAI-compatible (DeepSeek, OpenRouter). Renvoie le texte.

    Essaie d'abord response_format json_object ; si le fournisseur le refuse
    (HTTP 400), réessaie sans, dans la même tentative — l'appelant extrait
    alors le JSON du texte avec charger_json().
    """
    headers = {"Authorization": f"Bearer {cle}",
               "Content-Type": "application/json"}
    if headers_extra:
        headers.update(headers_extra)
    for tentative in range(1, len(delais) + 2):
        statut = None
        message = None
        for mode_json in (True, False):
            payload = {"model": modele,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": temperature}
            if mode_json:
                payload["response_format"] = {"type": "json_object"}
            try:
                r = requests.post(url, headers=headers, json=payload,
                                  timeout=TIMEOUT_S)
            except requests.RequestException as e:
                statut, message = "reseau", str(e)
                break
            if r.status_code == 200:
                try:
                    return r.json()["choices"][0]["message"]["content"]
                except (KeyError, IndexError, ValueError) as e:
                    raise RuntimeError(f"{modele} : réponse inattendue ({e})")
            if r.status_code == 400 and mode_json:
                continue
            statut, message = r.status_code, r.text[:200]
            break
        transitoire = statut == "reseau" or statut in (429, 500, 502, 503, 504)
        if transitoire and tentative <= len(delais):
            _attendre(delais, tentative, modele,
                      "erreur réseau" if statut == "reseau"
                      else f"HTTP {statut}", print)
            continue
        raise RuntimeError(f"{modele} : {message} (statut {statut})")
    raise RuntimeError(f"{modele} : échec après réessais")


def appeler_fournisseurs_openai(prompt, temperature, config=None,
                                journal=print):
    """Essaie DeepSeek, OpenRouter GLM, OpenRouter gratuit, routeur auto.

    Les couches sans clé sont ignorées (log, pas d'erreur). Lève
    AucunFournisseurSecours si aucune clé n'est définie, sinon l'erreur
    du dernier fournisseur essayé.
    """
    config = config or config_chaine()
    delais = config["delais_reessai_s"]
    essais = []
    cle_deepseek = os.environ.get("DEEPSEEK_API_KEY")
    if cle_deepseek:
        essais.append(("DeepSeek", DEEPSEEK_URL, cle_deepseek,
                       config["deepseek_modele"], None))
    else:
        journal("[llm] DeepSeek ignoré : DEEPSEEK_API_KEY absente")
    cle_openrouter = os.environ.get("OPENROUTER_API_KEY")
    if cle_openrouter:
        headers_or = {"HTTP-Referer":
                      "https://github.com/mcirservice-cloud/territoire-brut",
                      "X-Title": "Varus / Territoire Brut"}
        essais.append(("OpenRouter GLM", OPENROUTER_URL, cle_openrouter,
                       config["openrouter_modele_payant"], headers_or))
        for modele in config["openrouter_modeles_gratuits"]:
            essais.append((f"OpenRouter {modele}", OPENROUTER_URL,
                           cle_openrouter, modele, headers_or))
        if config["openrouter_secours_auto"]:
            essais.append(("OpenRouter auto", OPENROUTER_URL, cle_openrouter,
                           config["openrouter_secours_auto"], headers_or))
    else:
        journal("[llm] OpenRouter ignoré : OPENROUTER_API_KEY absente")
    if not essais:
        raise AucunFournisseurSecours(
            "Aucune clé DeepSeek/OpenRouter définie")
    derniere_erreur = None
    for nom, url, cle, modele, headers in essais:
        try:
            return _appeler_openai_compatible(url, cle, modele, prompt,
                                              temperature, delais, headers)
        except Exception as e:
            derniere_erreur = e
            journal(f"[llm] {nom} indisponible ({_resume_erreur(e)}), "
                    "secours suivant...")
    raise derniere_erreur


def appeler_chaine(prompt, temperature, cle_gemini, config=None,
                   journal=print):
    """Chaîne complète (Gemini REST + secours OpenAI). Renvoie le texte brut.

    Si aucun secours n'est configuré, l'erreur Gemini d'origine est levée.
    """
    config = config or config_chaine()
    delais = config["delais_reessai_s"]
    derniere_erreur = None
    for modele in config["gemini_modeles"]:
        try:
            return _appeler_gemini_rest(cle_gemini, modele, prompt,
                                        temperature, delais, journal)
        except Exception as e:
            derniere_erreur = e
            journal(f"[llm] {modele} indisponible ({_resume_erreur(e)}), "
                    "secours suivant...")
    try:
        return appeler_fournisseurs_openai(prompt, temperature, config, journal)
    except AucunFournisseurSecours:
        raise derniere_erreur
