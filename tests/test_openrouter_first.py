"""Tests OpenRouter-first — remplacement de Gemini comme moteur principal.

Contexte : le run n°9 a été annulé après 2 h sans progression à l'étape
script, parce qu'un appel SDK Gemini sans limite ne répondait plus.
Depuis, la génération du script passe par OpenRouter gratuit d'abord,
DeepSeek ensuite, Gemini REST borné en dernier recours uniquement.

Aucune vraie API n'est appelée : requests.post est simulé (mock).
Clés utilisées : des valeurs factices évidentes (« cle-test »), jamais
de vraies clés, jamais de clé réaliste.

Ce que ces tests prouvent :
(a) ordre OpenRouter d'abord (modèle gratuit configuré, puis routeur
    auto), avec le timeout explicite demandé ;
(b) repli vers Gemini REST borné si OpenRouter échoue ;
(c) un appel Gemini qui ne répond jamais déclenche le timeout et une
    erreur claire, jamais un blocage infini — preuve en trois parties,
    honnête sur ce qu'un test unitaire peut montrer avec un transport
    simulé : le timeout est bien passé à chaque requête HTTP, une
    exception Timeout est gérée par des réessais bornés puis une erreur
    claire, et AUCUN appel SDK ne subsiste dans script_gen.py (vérifié
    statiquement : sans SDK, aucun appel ne peut échapper au timeout) ;
(d) clés absentes : couche ignorée avec un log et repli gracieux,
    jamais de crash obscur.

Lancer depuis la racine de la copie : python3 tests/test_openrouter_first.py
"""

import json
import os
import sys
import time
import unittest
from unittest import mock

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
SRC = os.path.join(RACINE, "src")
sys.path.insert(0, SRC)

import requests  # noqa: E402
import llm_chain  # noqa: E402
import script_gen  # noqa: E402

GEMMA = "google/gemma-4-31b-it:free"
ROUTEUR = "openrouter/free"

CONFIG = {
    "gemini_modeles": ["gemini-3.8-flash", "gemini-3.5-flash"],
    "deepseek_modele": "deepseek-chat",
    "deepseek_modele_reflexion": "deepseek-chat",
    "openrouter_modele_payant": "z-ai/glm-5.3-flash",
    "openrouter_modeles_gratuits": [GEMMA],
    "openrouter_secours_auto": ROUTEUR,
    "openrouter_modele_generation": GEMMA,
    "openrouter_timeout_s": 300,
    "delais_reessai_s": [0, 0, 0],
}


def script_json_valide():
    """Script JSON minimal qui passe validate() (2 scènes, animations
    distinctes, durées 4-12 s, meta.titre présent)."""
    return json.dumps({
        "meta": {"titre": "Test"},
        "scenes": [
            {"id": 1, "narration": "narration 1",
             "animation": "ken_burns_zoom_in", "duree_estimee_s": 8},
            {"id": 2, "narration": "narration 2",
             "animation": "ken_burns_zoom_out", "duree_estimee_s": 8},
        ],
    })


class ReponseSimulee:
    """Réponse HTTP simulée (statut + json + texte)."""

    def __init__(self, statut, contenu):
        self.status_code = statut
        self._contenu = contenu
        self.text = contenu if isinstance(contenu, str) else json.dumps(contenu)

    def json(self):
        if isinstance(self._contenu, dict):
            return self._contenu
        return json.loads(self._contenu)


def reponse_openai(texte):
    return ReponseSimulee(200, {"choices": [{"message": {"content": texte}}]})


def reponse_gemini(texte):
    return ReponseSimulee(200, {"candidates": [{"content": {"parts": [
        {"text": texte}]}}]})


class TestOpenRouterFirst(unittest.TestCase):
    def test_openrouter_premier_et_timeout_300(self):
        """(a) OpenRouter gratuit d'abord, jamais Gemini si ça répond,
        timeout 300 passé à la requête."""
        appels = []

        def faux_post(url, headers=None, json=None, params=None, timeout=None):
            appels.append((url, (json or {}).get("model"), timeout))
            self.assertEqual(url, llm_chain.OPENROUTER_URL)
            return reponse_openai(script_json_valide())

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "cle-test"},
                             clear=True):
            with mock.patch.object(llm_chain.requests, "post",
                                   side_effect=faux_post):
                resultat = script_gen.generate_script(
                    "prompt", config=CONFIG)
        self.assertEqual(resultat["meta"]["titre"], "Test")
        self.assertTrue(appels, "aucun appel HTTP enregistré")
        self.assertEqual(appels[0][1], GEMMA,
                         "le premier modèle essayé doit être le gratuit configuré")
        self.assertEqual(appels[0][2], 300,
                         "le timeout OpenRouter demandé doit être passé")
        self.assertTrue(all("generativelanguage" not in url for url, _, _ in appels),
                        "Gemini ne doit pas être appelé quand OpenRouter répond")

    def test_repli_routeur_auto_si_modele_gratuit_echoue(self):
        """(a) Si le modèle gratuit échoue (503), le routeur auto prend le relais."""
        modeles_essayes = []

        def faux_post(url, headers=None, json=None, params=None, timeout=None):
            modele = (json or {}).get("model")
            modeles_essayes.append(modele)
            if modele == GEMMA:
                return ReponseSimulee(503, "surcharge")
            return reponse_openai(script_json_valide())

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "cle-test"},
                             clear=True):
            with mock.patch.object(llm_chain.requests, "post",
                                   side_effect=faux_post):
                resultat = script_gen.generate_script(
                    "prompt", config=CONFIG)
        self.assertEqual(resultat["meta"]["titre"], "Test")
        self.assertEqual(modeles_essayes[0], GEMMA)
        self.assertIn(ROUTEUR, modeles_essayes,
                      "le routeur auto doit être essayé après l'échec du gratuit")

    def test_repli_gemini_borne_si_openrouter_echoue(self):
        """(b) Si tout OpenRouter échoue, Gemini REST borné prend le relais."""
        urls = []

        def faux_post(url, headers=None, json=None, params=None, timeout=None):
            urls.append(url)
            if "openrouter" in url:
                return ReponseSimulee(503, "surcharge")
            return reponse_gemini(script_json_valide())

        env = {"OPENROUTER_API_KEY": "cle-test", "GEMINI_API_KEY": "cle-test"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(llm_chain.requests, "post",
                                   side_effect=faux_post):
                resultat = script_gen.generate_script(
                    "prompt", config=CONFIG)
        self.assertEqual(resultat["meta"]["titre"], "Test")
        self.assertEqual(urls[0], llm_chain.OPENROUTER_URL,
                         "OpenRouter doit être essayé avant Gemini")
        self.assertTrue(any("generativelanguage" in u for u in urls),
                        "Gemini REST doit être appelé en dernier recours")

    def test_gemini_borne_timeout_jamais_bloque(self):
        """(c) Un appel Gemini qui ne répond jamais : timeout déclenché,
        réessais bornés, erreur claire — et surtout, retour rapide."""
        timeouts_vus = []

        def faux_post(url, headers=None, json=None, params=None, timeout=None):
            timeouts_vus.append(timeout)
            raise requests.exceptions.Timeout("lecture expirée (simulée)")

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-test"},
                             clear=True):
            with mock.patch.object(llm_chain.requests, "post",
                                   side_effect=faux_post):
                debut = time.monotonic()
                with self.assertRaises(RuntimeError):
                    script_gen.generate_script("prompt", config=CONFIG)
                duree = time.monotonic() - debut
        self.assertLess(duree, 30,
                        "la génération doit échouer vite, jamais pendre")
        self.assertTrue(timeouts_vus, "aucune requête HTTP enregistrée")
        self.assertTrue(all(t == 180 for t in timeouts_vus),
                        "chaque requête Gemini doit porter le timeout 180 s")

    def test_aucun_appel_sdk_dans_script_gen(self):
        """(c) Preuve statique : plus aucun appel SDK dans script_gen.py,
        donc aucun appel ne peut échapper au timeout HTTP."""
        chemin = os.path.join(SRC, "script_gen.py")
        with open(chemin, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("genai", source,
                         "script_gen.py ne doit plus importer le SDK")
        self.assertNotIn("generate_content", source,
                         "script_gen.py ne doit plus appeler le SDK")
        self.assertIn("appeler_generation", source,
                      "script_gen.py doit passer par la chaîne bornée")
        chemin_chaine = os.path.join(SRC, "llm_chain.py")
        with open(chemin_chaine, encoding="utf-8") as f:
            source_chaine = f.read()
        self.assertNotIn("def appeler_chaine", source_chaine,
                         "l'ancien chemin Gemini-d'abord doit avoir disparu")

    def test_cles_absentes_repli_gracieux(self):
        """(d) Couche sans clé ignorée avec un log, repli gracieux ;
        aucune clé du tout = erreur claire, pas de crash obscur."""
        journal = []

        def faux_post(url, headers=None, json=None, params=None, timeout=None):
            return reponse_gemini(script_json_valide())

        # OpenRouter absent, Gemini présent : repli gracieux vers Gemini.
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-test"},
                             clear=True):
            with mock.patch.object(llm_chain.requests, "post",
                                   side_effect=faux_post):
                texte = llm_chain.appeler_generation(
                    "prompt", 0.7, CONFIG, journal=journal.append)
        self.assertIn("Test", texte,
                      "le repli vers Gemini doit renvoyer le script")
        self.assertTrue(texte)
        self.assertTrue(any("OpenRouter" in ligne and "absente" in ligne
                            for ligne in journal),
                        "l'absence de clé OpenRouter doit être journalisée")
        # Aucune clé : erreur claire mentionnant les trois clés.
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                script_gen.generate_script("prompt", config=CONFIG)
        message = str(ctx.exception)
        self.assertIn("OPENROUTER_API_KEY", message)
        self.assertIn("GEMINI_API_KEY", message)

    def test_reflexion_gemini_bornee(self):
        """Fact-check : l'appel Gemini de réflexion porte aussi un timeout."""
        timeouts_vus = []
        verdict_json = json.dumps({"verdict": "ok", "problemes": []})

        def faux_post(url, headers=None, json=None, params=None, timeout=None):
            timeouts_vus.append(timeout)
            return reponse_gemini(verdict_json)

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-test"},
                             clear=True):
            with mock.patch.object(llm_chain.requests, "post",
                                   side_effect=faux_post):
                resultat = llm_chain.appeler_reflexion(
                    "prompt", 0.0, CONFIG)
        self.assertEqual(resultat["verdict"] if isinstance(resultat, dict)
                         else json.loads(resultat)["verdict"], "ok")
        self.assertTrue(timeouts_vus)
        self.assertTrue(all(t == 180 for t in timeouts_vus),
                        "la réflexion Gemini doit aussi être bornée (180 s)")


if __name__ == "__main__":
    unittest.main()
