"""Chaîne de secours LLM partagée par script_gen.py et fact_check.py.

GÉNÉRATION du script (appeler_generation, depuis le remplacement de
Gemini décidé le 2026-10-29 après le blocage du run n°9) :
1. OpenRouter gratuit (clé OPENROUTER_API_KEY) — modèle gratuit configuré
   puis routeur automatique openrouter/free. Premier qui répond gagne.
2. DeepSeek deepseek-chat (clé DEEPSEEK_API_KEY, endpoint OpenAI-compatible).
3. Gemini REST BORNÉ en DERNIER recours uniquement (clé GEMINI_API_KEY,
   modèles configurés, timeout par requête). L'ancien appel SDK sans
   limite, qui a bloqué le run n°9 pendant 2 h, est INTERDIT : il ne
   reste AUCUN appel SDK dans cette chaîne, tous les appels passent par
   requests avec timeout explicite.

RÉFLEXION fact-check (appeler_reflexion, ordre inchangé) :
1. DeepSeek (modèle de réflexion configuré, deepseek-chat par défaut).
2. Gemini REST borné.
3. OpenRouter gratuit.

Règles :
- Une couche dont la clé est absente est ignorée avec une ligne de log,
  jamais une erreur bloquante.
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
DEEPSEEK_MODELE_REFLEXION_DEFAUT = "deepseek-chat"  # réflexion fact-check :
# deepseek-chat (V3, quasi gratuit) par défaut. "deepseek-reasoner" est
# accepté via settings (plus lent, plus cher) mais JAMAIS le défaut.
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELE_GLM = "z-ai/glm-5.3-flash"
OPENROUTER_MODELE_GRATUIT_DEFAUT = "google/gemma-4-31b-it:free"
OPENROUTER_SECOURS_AUTO = "openrouter/free"
OPENROUTER_MODELE_GENERATION_DEFAUT = "google/gemma-4-31b-it:free"
OPENROUTER_TIMEOUT_GENERATION_S = 300
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
        "deepseek_modele_reflexion":
            brutes.get("deepseek_modele_reflexion") or
            DEEPSEEK_MODELE_REFLEXION_DEFAUT,
        "openrouter_modele_payant": brutes.get("openrouter_modele_payant") or
        OPENROUTER_MODELE_GLM,
        "openrouter_modeles_gratuits": gratuits,
        "openrouter_secours_auto": secours_auto or None,
        "openrouter_modele_generation":
            brutes.get("openrouter_modele_generation") or
            OPENROUTER_MODELE_GENERATION_DEFAUT,
        "openrouter_timeout_s":
            brutes.get("openrouter_timeout_s") or
            OPENROUTER_TIMEOUT_GENERATION_S,
        "delais_reessai_s": brutes.get("delais_reessai_s") or
        list(DELAIS_REESSAI_S),
    }


def au_moins_une_cle():
    """Vrai si au moins un fournisseur de la chaîne a sa clé définie."""
    return any(os.environ.get(cle) for cle in
               ("GEMINI_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"))


def _attendre(delais, tentative, modele, raison, journal):
    attente = delais[tentative - 1]
    journal(f"[llm] {modele} : {raison}, nouvel essai dans {attente}s "
            f"(tentative {tentative}/{len(delais) + 1})")
    time.sleep(attente)


def _appeler_gemini_rest(cle, modele, prompt, temperature, delais,
                         journal=print):
    """Gemini via REST, avec réessais sur erreurs temporaires. Renvoie le texte.

    C'est LE SEUL chemin Gemini autorisé : chaque requête HTTP porte un
    timeout explicite (TIMEOUT_S), donc un appel Gemini ne peut plus
    pendre indéfiniment — c'est ce qui a bloqué le run n°9 (appel SDK
    sans limite, aujourd'hui supprimé de la génération).
    """
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
                               headers_extra=None, timeout_s=None):
    """Endpoint OpenAI-compatible (DeepSeek, OpenRouter). Renvoie le texte.

    Essaie d'abord response_format json_object ; si le fournisseur le refuse
    (HTTP 400), réessaie sans, dans la même tentative — l'appelant extrait
    alors le JSON du texte avec charger_json().
    Chaque requête HTTP porte un timeout explicite (timeout_s, sinon
    TIMEOUT_S) : aucun appel ne peut pendre indéfiniment.
    """
    headers = {"Authorization": f"Bearer {cle}",
               "Content-Type": "application/json"}
    if headers_extra:
        headers.update(headers_extra)
    timeout = timeout_s or TIMEOUT_S
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
                                  timeout=timeout)
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


def appeler_openrouter_gratuit(prompt, temperature, config=None,
                               journal=print):
    """OpenRouter GRATUIT en premier pour la génération du script.

    Essaie le modèle gratuit configuré (openrouter_modele_generation),
    puis les autres modèles gratuits configurés, puis le routeur
    automatique openrouter/free. Chaque requête porte le timeout
    openrouter_timeout_s : un modèle qui ne répond jamais déclenche le
    repli suivant, jamais un blocage.

    Lève AucunFournisseurSecours si OPENROUTER_API_KEY est absente
    (couche ignorée avec un log), sinon l'erreur du dernier modèle essayé.
    """
    config = config or config_chaine()
    delais = config["delais_reessai_s"]
    timeout = config.get("openrouter_timeout_s",
                         OPENROUTER_TIMEOUT_GENERATION_S)
    cle = os.environ.get("OPENROUTER_API_KEY")
    if not cle:
        journal("[llm] OpenRouter gratuit ignoré : OPENROUTER_API_KEY absente")
        raise AucunFournisseurSecours("OPENROUTER_API_KEY absente")
    headers = {"HTTP-Referer":
               "https://github.com/mcirservice-cloud/territoire-brut",
               "X-Title": "Varus / Territoire Brut"}
    modeles = [config.get("openrouter_modele_generation",
                          OPENROUTER_MODELE_GENERATION_DEFAUT)]
    for modele in config.get("openrouter_modeles_gratuits",
                             [OPENROUTER_MODELE_GRATUIT_DEFAUT]):
        if modele not in modeles:
            modeles.append(modele)
    secours_auto = config.get("openrouter_secours_auto",
                              OPENROUTER_SECOURS_AUTO)
    if secours_auto and secours_auto not in modeles:
        modeles.append(secours_auto)
    derniere_erreur = None
    for modele in modeles:
        try:
            return _appeler_openai_compatible(
                OPENROUTER_URL, cle, modele, prompt, temperature, delais,
                headers, timeout_s=timeout)
        except Exception as e:
            derniere_erreur = e
            journal(f"[llm] OpenRouter {modele} indisponible "
                    f"({_resume_erreur(e)}), secours suivant...")
    raise derniere_erreur


def appeler_generation(prompt, temperature, config=None, journal=print):
    """GÉNÉRATION du script — ordre en vigueur depuis le remplacement
    de Gemini (décision Sam, 2026-10-29, après le blocage du run n°9) :

    1. OpenRouter gratuit (modèle configuré puis routeur auto) ;
    2. DeepSeek (si DEEPSEEK_API_KEY) — ignoré sans clé, jamais bloquant ;
    3. Gemini REST BORNÉ en dernier recours uniquement (timeout par
       requête via _appeler_gemini_rest). AUCUN appel SDK : un appel
       Gemini ne peut plus pendre indéfiniment.

    Une couche sans clé est ignorée avec un log, jamais un crash.
    Lève AucunFournisseurSecours si aucune clé n'est définie, sinon
    l'erreur du dernier fournisseur essayé.
    """
    config = config or config_chaine()
    delais = config["delais_reessai_s"]
    derniere_erreur = None
    try:
        return appeler_openrouter_gratuit(prompt, temperature, config, journal)
    except AucunFournisseurSecours:
        pass
    except Exception as e:
        derniere_erreur = e
        journal(f"[llm] OpenRouter gratuit indisponible "
                f"({_resume_erreur(e)}), secours suivant...")
    cle_deepseek = os.environ.get("DEEPSEEK_API_KEY")
    if cle_deepseek:
        try:
            return _appeler_openai_compatible(
                DEEPSEEK_URL, cle_deepseek, config["deepseek_modele"],
                prompt, temperature, delais)
        except Exception as e:
            derniere_erreur = e
            journal(f"[llm] DeepSeek indisponible ({_resume_erreur(e)}), "
                    "secours suivant...")
    else:
        journal("[llm] DeepSeek ignoré : DEEPSEEK_API_KEY absente")
    cle_gemini = os.environ.get("GEMINI_API_KEY")
    if cle_gemini:
        for modele in config["gemini_modeles"]:
            try:
                return _appeler_gemini_rest(cle_gemini, modele, prompt,
                                            temperature, delais, journal)
            except Exception as e:
                derniere_erreur = e
                journal(f"[llm] {modele} indisponible "
                        f"({_resume_erreur(e)}), secours suivant...")
    else:
        journal("[llm] Gemini ignoré : GEMINI_API_KEY absente")
    if derniere_erreur is None:
        raise AucunFournisseurSecours(
            "Aucune clé LLM définie (OPENROUTER_API_KEY, DEEPSEEK_API_KEY "
            "ou GEMINI_API_KEY)")
    raise derniere_erreur


def appeler_reflexion(prompt, temperature, config=None, journal=print):
    """Chemin « réflexion » pour le fact-check et la correction.

    Ordre (règle : chaque tâche a au moins deux alternatives) :
    1. DeepSeek (modèle de réflexion configuré, deepseek-chat par défaut)
       quand DEEPSEEK_API_KEY est définie — la réflexion critique
       (relecture, incohérences) y est meilleure marché que Gemini.
    2. Gemini (modèles configurés) sinon ou en secours.
    3. OpenRouter gratuit (modèles :free + routeur auto) en dernier
       recours — la réflexion reste gratuite d'abord (le modèle payant
       GLM ne fait plus partie du chemin de génération, choix gratuit
       de Sam).

    Une clé absente n'est jamais bloquante : la couche est ignorée avec
    une ligne de log et on passe à la suivante. Lève RuntimeError
    seulement si AUCUN fournisseur n'est utilisable.
    """
    config = config or config_chaine()
    delais = config["delais_reessai_s"]
    modele_reflexion = config["deepseek_modele_reflexion"]
    if modele_reflexion == "deepseek-reasoner":
        journal("[reflexion] deepseek-reasoner demandé : plus lent et plus "
                "cher que deepseek-chat (optionnel, non recommandé).")
    cle_deepseek = os.environ.get("DEEPSEEK_API_KEY")
    if cle_deepseek:
        try:
            return _appeler_openai_compatible(
                DEEPSEEK_URL, cle_deepseek, modele_reflexion, prompt,
                temperature, delais)
        except Exception as e:
            journal(f"[reflexion] DeepSeek indisponible "
                    f"({_resume_erreur(e)}), repli Gemini...")
    else:
        journal("[reflexion] DeepSeek ignoré : DEEPSEEK_API_KEY absente, "
                "repli Gemini.")
    cle_gemini = os.environ.get("GEMINI_API_KEY")
    if cle_gemini:
        for modele in config["gemini_modeles"]:
            try:
                return _appeler_gemini_rest(cle_gemini, modele, prompt,
                                            temperature, delais, journal)
            except Exception as e:
                journal(f"[reflexion] {modele} indisponible "
                        f"({_resume_erreur(e)}), secours suivant...")
    else:
        journal("[reflexion] Gemini ignoré : GEMINI_API_KEY absente.")
    cle_openrouter = os.environ.get("OPENROUTER_API_KEY")
    if cle_openrouter:
        headers_or = {"HTTP-Referer":
                      "https://github.com/mcirservice-cloud/territoire-brut",
                      "X-Title": "Varus / Territoire Brut"}
        modeles = list(config["openrouter_modeles_gratuits"])
        if config["openrouter_secours_auto"]:
            modeles.append(config["openrouter_secours_auto"])
        for modele in modeles:
            try:
                return _appeler_openai_compatible(
                    OPENROUTER_URL, cle_openrouter, modele, prompt,
                    temperature, delais, headers_or)
            except Exception as e:
                journal(f"[reflexion] OpenRouter {modele} indisponible "
                        f"({_resume_erreur(e)}), secours suivant...")
    else:
        journal("[reflexion] OpenRouter ignoré : OPENROUTER_API_KEY absente.")
    raise RuntimeError(
        "Réflexion impossible : aucun fournisseur disponible "
        "(clés DeepSeek/Gemini/OpenRouter absentes ou toutes en échec).")
