"""Tests de fumée v2 — revue complète Varus (puissance / vitesse / complétude).

Aucune vraie API n'est appelée : réseau et LLM sont simulés (mocks).
Couverture exigée par la revue :
- timeouts subprocess (exec_utils) : une commande bloquée est tuée vite ;
- écritures checkpoint progressives (checkpoint) : script, étapes,
  blocage complet, échec ;
- boucle de correction BORNÉE (fact_check) : arrêt après max 2
  tentatives, jamais de publication avec avertissement ;
- réflexion DeepSeek d'abord (llm_chain) + replis Gemini / OpenRouter ;
- validation frames (render) : vidéo noire rejetée, vidéo couleur OK ;
- budget de recherche visuelle (assets) : abandon rapide, sans appel.
- intégration : run() bloqué sur fact-check -> script.json +
  blocage.json présents dans output/.

Lancer depuis la racine de la copie : python3 tests/test_smoke_v2.py
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
SRC = os.path.join(RACINE, "src")
sys.path.insert(0, SRC)

from exec_utils import CommandeTropLongue, check_output, run as run_cmd  # noqa: E402
from checkpoint import Checkpoint  # noqa: E402
import fact_check  # noqa: E402
import llm_chain  # noqa: E402
import assets  # noqa: E402
from render import luminance_moyenne_image, valider_frames  # noqa: E402


def script_exemple(nb_scenes=3):
    return {
        "meta": {"titre": "Test", "description": "d",
                 "miniature": {"texte": "TEST ANIMAL", "emotion": "danger"}},
        "scenes": [{"id": i + 1, "narration": f"narration {i + 1}",
                    "visuel": {"requete_pexels_en": "cheetah running",
                               "requete_alternative_en": "cheetah"},
                    "animation": "ken_burns_zoom_in",
                    "duree_estimee_s": 8} for i in range(nb_scenes)],
    }


class TestExecUtils(unittest.TestCase):
    def test_timeout_tue_la_commande(self):
        with self.assertRaises(CommandeTropLongue):
            run_cmd(["sleep", "5"], timeout=1)

    def test_succes(self):
        resultat = run_cmd(["echo", "ok"], timeout=10, capture_output=True,
                           text=True)
        self.assertEqual(resultat.returncode, 0)

    def test_check_output_timeout(self):
        with self.assertRaises(CommandeTropLongue):
            check_output(["sleep", "5"], timeout=1)


class TestCheckpoint(unittest.TestCase):
    def test_ecritures_progressives(self):
        with tempfile.TemporaryDirectory() as tmp:
            sortie = os.path.join(tmp, "output")
            cp = Checkpoint(sortie)
            script = script_exemple()
            cp.sauver_script(script)
            cp.etape("faits", {"sujet": "Guépard"})
            cp.sauver_blocage(script, [2],
                              [{"scene_id": 2, "affirmation": "x"}],
                              {"sujet": "Guépard"})
            cp.sauver_echec(script, "voix", RuntimeError("boom"))
            for nom in ("script.json", "checkpoint.json", "blocage.json",
                        "echec.json"):
                self.assertTrue(os.path.exists(os.path.join(sortie, nom)),
                                nom)
            with open(os.path.join(sortie, "checkpoint.json")) as f:
                etat = json.load(f)
            self.assertIn("faits", etat["etapes"])
            with open(os.path.join(sortie, "blocage.json")) as f:
                blocage = json.load(f)
            self.assertEqual(blocage["scenes_signalees"], [2])
            self.assertEqual(len(blocage["problemes"]), 1)
            with open(os.path.join(sortie, "echec.json")) as f:
                echec = json.load(f)
            self.assertEqual(echec["etape"], "voix")
            self.assertIn("boom", echec["erreur"])
            restes = [n for n in os.listdir(sortie) if n.endswith(".tmp")]
            self.assertEqual(restes, [])


class TestBoucleCorrection(unittest.TestCase):
    def test_arret_apres_max_2_tentatives(self):
        script = script_exemple()
        script["scenes"][1]["a_verifier"] = True
        initial = {"verdict": "a_corriger",
                   "problemes": [{"scene_id": 2, "affirmation": "x"}]}
        with mock.patch.object(
                fact_check, "corriger_scenes",
                return_value=[{"scene_id": 2,
                               "narration": "narration corrigée"}]), \
            mock.patch.object(
                fact_check, "fact_check",
                return_value={"verdict": "a_corriger",
                              "problemes": [{"scene_id": 2,
                                             "affirmation": "x"}]}):
            restantes, journal = fact_check.corriger_et_reverifier(
                script, "faits", "prompt", initial, max_tentatives=2)
        self.assertEqual(restantes, [2])
        self.assertEqual(len(journal), 2)

    def test_sortie_anticipee_si_corrige(self):
        script = script_exemple()
        script["scenes"][1]["a_verifier"] = True
        initial = {"verdict": "a_corriger",
                   "problemes": [{"scene_id": 2, "affirmation": "x"}]}
        appels = {"n": 0}

        def faux_check(*_a, **_k):
            appels["n"] += 1
            if appels["n"] == 1:
                return {"verdict": "a_corriger",
                        "problemes": [{"scene_id": 2, "affirmation": "x"}]}
            return {"verdict": "ok", "problemes": []}

        with mock.patch.object(
                fact_check, "corriger_scenes",
                return_value=[{"scene_id": 2,
                               "narration": "narration corrigée"}]), \
            mock.patch.object(fact_check, "fact_check",
                              side_effect=faux_check):
            restantes, journal = fact_check.corriger_et_reverifier(
                script, "faits", "prompt", initial, max_tentatives=2)
        self.assertEqual(restantes, [])
        self.assertEqual(len(journal), 2)

    def test_correction_scene_inconnue_ignoree(self):
        script = script_exemple()
        appliquees = fact_check.appliquer_corrections(
            script, [{"scene_id": 99, "narration": "inutile"}])
        self.assertEqual(appliquees, 0)


class TestReflexion(unittest.TestCase):
    CONFIG = {"deepseek_modele_reflexion": "deepseek-chat",
              "gemini_modeles": ["gemini-3.8-flash"],
              "openrouter_modeles_gratuits": [],
              "openrouter_secours_auto": None,
              "delais_reessai_s": []}

    def test_deepseek_d_abord_quand_cle_presente(self):
        appels = []

        def faux_openai(url, _cle, modele, _prompt, _temp, _delais,
                        _headers=None):
            appels.append((url, modele))
            return '{"ok": true}'

        with mock.patch.object(llm_chain, "_appeler_openai_compatible",
                               side_effect=faux_openai), \
            mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fausse-cle"},
                            clear=False):
            env = dict(os.environ)
            env.pop("GEMINI_API_KEY", None)
            env.pop("OPENROUTER_API_KEY", None)
            with mock.patch.dict(os.environ, env, clear=True):
                llm_chain.appeler_reflexion("prompt", 0.0, self.CONFIG,
                                            journal=lambda *a: None)
        self.assertEqual(appels[0],
                         (llm_chain.DEEPSEEK_URL, "deepseek-chat"))

    def test_reasoner_optionnel_si_configure(self):
        appels = []

        def faux_openai(url, _cle, modele, _prompt, _temp, _delais,
                        _headers=None):
            appels.append(modele)
            return '{"ok": true}'

        config = dict(self.CONFIG)
        config["deepseek_modele_reflexion"] = "deepseek-reasoner"
        with mock.patch.object(llm_chain, "_appeler_openai_compatible",
                               side_effect=faux_openai), \
            mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fausse-cle"},
                            clear=False):
            llm_chain.appeler_reflexion("prompt", 0.0, config,
                                        journal=lambda *a: None)
        self.assertEqual(appels[0], "deepseek-reasoner")

    def test_repli_gemini_sans_cle_deepseek(self):
        with mock.patch.object(llm_chain, "_appeler_gemini_rest",
                               return_value="texte-gemini") as gemini, \
            mock.patch.object(llm_chain, "_appeler_openai_compatible",
                              side_effect=AssertionError("ne doit pas "
                                                         "appeler")):
            env = {"GEMINI_API_KEY": "fausse-cle"}
            with mock.patch.dict(os.environ, env, clear=True):
                resultat = llm_chain.appeler_reflexion(
                    "prompt", 0.0, self.CONFIG, journal=lambda *a: None)
        self.assertEqual(resultat, "texte-gemini")
        gemini.assert_called_once()

    def test_erreur_si_aucune_cle(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                llm_chain.appeler_reflexion("prompt", 0.0, self.CONFIG,
                                            journal=lambda *a: None)


class TestValidationFrames(unittest.TestCase):
    def test_luminance_pure(self):
        from PIL import Image
        self.assertEqual(luminance_moyenne_image(
            Image.new("RGB", (10, 10), (0, 0, 0))), 0.0)
        self.assertEqual(luminance_moyenne_image(
            Image.new("RGB", (10, 10), (255, 255, 255))), 255.0)

    def test_video_noire_rejetee_couleur_acceptee(self):
        import shutil
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg absent")
        with tempfile.TemporaryDirectory() as tmp:
            noire = os.path.join(tmp, "noire.mp4")
            couleur = os.path.join(tmp, "couleur.mp4")
            run_cmd(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", "color=c=0x000000:s=320x240:d=2",
                     "-c:v", "mpeg4", "-pix_fmt", "yuv420p", noire],
                    timeout=60)
            run_cmd(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", "color=c=0xFF0000:s=320x240:d=2",
                     "-c:v", "mpeg4", "-pix_fmt", "yuv420p", couleur],
                    timeout=60)
            rapport_noir = valider_frames(noire, echantillons=4)
            self.assertFalse(rapport_noir["ok"])
            rapport_couleur = valider_frames(couleur, echantillons=4)
            self.assertTrue(rapport_couleur["ok"])


class TestBudgetVisuel(unittest.TestCase):
    def test_budget_negatif_aucun_appel(self):
        def interdit(*_a, **_k):
            raise AssertionError("aucun fournisseur ne doit être appelé")

        with mock.patch.object(assets, "wikimedia_video",
                               side_effect=interdit), \
            mock.patch.object(assets, "pixabay_video",
                              side_effect=interdit), \
            mock.patch.object(assets, "pexels_video",
                              side_effect=interdit), \
            mock.patch.object(assets, "openverse_photo",
                              side_effect=interdit), \
            mock.patch.object(assets, "wikimedia_photo",
                              side_effect=interdit), \
            mock.patch.object(assets, "pixabay_photo",
                              side_effect=interdit), \
            mock.patch.object(assets, "pexels_photo",
                              side_effect=interdit):
            chemin, type_visuel = assets.trouver_visuel(
                "cheetah running fast", budget_s=-1)
        self.assertIsNone(chemin)
        self.assertIsNone(type_visuel)


class TestIntegrationBlocage(unittest.TestCase):
    def test_run_bloque_sauvegarde_tout(self):
        # Stubs des SDK Google (absents de cette VM, présents sur le
        # runner) : l'upload n'est jamais atteint sur le chemin de
        # blocage, les stubs servent juste à l'import de upload.py.
        import types

        def _stub(nom, **attrs):
            mod = types.ModuleType(nom)
            for cle, valeur in attrs.items():
                setattr(mod, cle, valeur)
            sys.modules[nom] = mod
            return mod

        google_mod = _stub("google")
        oauth2_mod = _stub("google.oauth2")
        cred_mod = _stub("google.oauth2.credentials", Credentials=object)
        google_mod.oauth2 = oauth2_mod
        oauth2_mod.credentials = cred_mod
        _stub("googleapiclient")
        _stub("googleapiclient.discovery", build=lambda *a, **k: None)
        _stub("googleapiclient.http", MediaFileUpload=object)

        import main as main_module
        import topics as topics_module
        import facts as facts_module
        import script_gen as script_gen_module

        with tempfile.TemporaryDirectory() as tmp:
            sortie = os.path.join(tmp, "output")
            cache = os.path.join(tmp, "cache")
            faux_parametres = {
                "dossiers": {"cache": cache, "sortie": sortie},
                "publication": {"privacy_status": "private",
                                "delai_publication_heures": 2},
                "fact_check": {"correction_max_tentatives": 2},
                "validation_frames": {},
            }
            sujet = {"nom": "Guépard", "nom_latin": "Acinonyx jubatus",
                     "page_wikipedia": "Guépard", "duree": 8,
                     "angle": "survie", "tension": 3, "statut": "à produire"}
            script = script_exemple(nb_scenes=1)
            with mock.patch.dict(os.environ, {
                    "GEMINI_API_KEY": "test", "YT_CLIENT_ID": "test",
                    "YT_CLIENT_SECRET": "test",
                    "YT_REFRESH_TOKEN": "test"}, clear=False), \
                mock.patch.object(main_module, "charger_parametres",
                                  return_value=faux_parametres), \
                mock.patch.object(topics_module, "lire_csv",
                                  return_value=[sujet]), \
                mock.patch.object(facts_module, "fetch_facts",
                                  return_value="faits test"), \
                mock.patch.object(script_gen_module, "construire_prompt",
                                  return_value="prompt"), \
                mock.patch.object(script_gen_module, "generate_script",
                                  return_value=script), \
                mock.patch.object(fact_check, "fact_check",
                                  return_value={
                                      "verdict": "a_corriger",
                                      "problemes": [{"scene_id": 1,
                                                     "affirmation": "x",
                                                     "raison": "y"}]}), \
                mock.patch.object(fact_check, "corriger_scenes",
                                  return_value=[]):
                with self.assertRaises(RuntimeError) as ctx:
                    main_module.run(sujet_nom="Guépard", sans_upload=True)
            self.assertIn("bloquée", str(ctx.exception))
            for nom in ("script.json", "blocage.json", "checkpoint.json"):
                self.assertTrue(
                    os.path.exists(os.path.join(sortie, nom)), nom)
            with open(os.path.join(sortie, "blocage.json")) as f:
                blocage = json.load(f)
            self.assertEqual(blocage["scenes_signalees"], [1])
            self.assertEqual(len(blocage["problemes"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
