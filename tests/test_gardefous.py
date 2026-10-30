"""Tests garde-fous : chaque panne qui rend la vidéo impossible doit
annuler immédiatement, avec son code, sans attendre la fin du run."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gardefous import (EchecGardeFou, verifier_script,
                       verifier_scenes_min, verifier_manifests)


def _script(nb=30):
    return {
        "meta": {"titre": "T", "description": "D",
                 "miniature": {"texte": "X"}},
        "scenes": [{"id": i, "narration": "Le guépard court vite.",
                    "visuel": {"requete_pexels_en": "cheetah running"},
                    "duree_estimee_s": 8} for i in range(1, nb + 1)],
    }


class TestGardeFous(unittest.TestCase):
    def test_script_valide_ok(self):
        self.assertTrue(verifier_script(_script(), duree_cible_min=4))

    def test_script_sans_scene_G004(self):
        with self.assertRaises(EchecGardeFou) as ctx:
            verifier_script({"meta": {}, "scenes": []})
        self.assertEqual(ctx.exception.code, "G004")

    def test_script_ids_non_consecutifs_G004(self):
        sc = _script(3)
        sc["scenes"][1]["id"] = 99
        with self.assertRaises(EchecGardeFou) as ctx:
            verifier_script(sc)
        self.assertEqual(ctx.exception.code, "G004")

    def test_script_sans_narration_G004(self):
        sc = _script(3)
        sc["scenes"][0]["narration"] = "   "
        with self.assertRaises(EchecGardeFou) as ctx:
            verifier_script(sc)
        self.assertEqual(ctx.exception.code, "G004")

    def test_script_sans_meta_G004(self):
        sc = _script(3)
        sc["meta"] = {}
        with self.assertRaises(EchecGardeFou) as ctx:
            verifier_script(sc)
        self.assertEqual(ctx.exception.code, "G004")

    def test_moins_de_20_scenes_G005(self):
        with self.assertRaises(EchecGardeFou) as ctx:
            verifier_scenes_min(_script(5)["scenes"])
        self.assertEqual(ctx.exception.code, "G005")

    def test_manifest_incomplet_G008(self):
        # Fichiers temporaires existants pour les scènes présentes.
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            chemin = f.name
        try:
            manifests = [{"scenes": [
                {"id": 1, "segment": chemin, "voice_path": chemin}]}]
            with self.assertRaises(EchecGardeFou) as ctx:
                verifier_manifests(_script(2)["scenes"], manifests)
            self.assertEqual(ctx.exception.code, "G008")
        finally:
            os.remove(chemin)


if __name__ == "__main__":
    unittest.main()
