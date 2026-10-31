"""Tests auto-correcteur + dépendances + correctif G013.

G013 (run #4 + reprise #1) : cache/ inexistant dans un job
assemble frais -> concatener_voix plantait en 0-1 s sur
cache/voix_complete.mp3.txt. Le correctif doit créer les dossiers.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from auto_correcteur import maintenance
from dependances import verifier_dependances


class TestAutoCorrecteur(unittest.TestCase):
    def test_maintenance_cree_dossiers_et_repare_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            sortie = os.path.join(tmp, "output")
            os.makedirs(sortie)
            # Script avec animation répétée + durée hors plage.
            script = {
                "meta": {"titre": "T", "description": "D",
                         "miniature": {"texte": "X"}},
                "scenes": [
                    {"id": 1, "narration": "Phrase un.",
                     "visuel": {"requete_pexels_en": "cheetah"},
                     "duree_estimee_s": 8,
                     "animation": "ken_burns_zoom_in"},
                    {"id": 2, "narration": "Phrase deux.",
                     "visuel": {"requete_pexels_en": "cheetah"},
                     "duree_estimee_s": 20,
                     "animation": "ken_burns_zoom_in"}]}
            with open(os.path.join(sortie, "script.json"), "w",
                      encoding="utf-8") as f:
                json.dump(script, f)
            # Shard avec une entrée valide + une entrée manquante.
            shard = os.path.join(sortie, "shard-0")
            os.makedirs(shard)
            seg = os.path.join(shard, "scene_001.mp4")
            voix = os.path.join(shard, "scene_001.mp3")
            open(seg, "wb").write(b"x")
            open(voix, "wb").write(b"x")
            man = {"shard": 0, "scenes": [
                {"id": 1, "segment": seg, "voice_path": voix},
                {"id": 2, "segment": os.path.join(shard, "manquant.mp4"),
                 "voice_path": os.path.join(shard, "manquant.mp3")}]}
            with open(os.path.join(shard, "manifest.json"), "w",
                      encoding="utf-8") as f:
                json.dump(man, f)
            rapport = maintenance(sortie, os.path.join(tmp, "cache"))
            self.assertTrue(os.path.isdir(os.path.join(tmp, "cache")))
            self.assertTrue(os.path.isdir(
                os.path.join(tmp, "cache", "voice")))
            self.assertTrue(rapport["corrections"])
            with open(os.path.join(shard, "manifest.json"),
                      encoding="utf-8") as f:
                man2 = json.load(f)
            self.assertEqual(len(man2["scenes"]), 1)
            self.assertTrue(os.path.exists(
                os.path.join(sortie, "maintenance.json")))

    def test_verifier_dependances_ne_plante_pas(self):
        manq_sys, manq_py, manq_cfg = verifier_dependances()
        self.assertIsInstance(manq_sys, list)
        self.assertIsInstance(manq_py, list)
        self.assertIsInstance(manq_cfg, list)

    def test_concatener_voix_cree_dossier_cache_manquant(self):
        # Régression G013 : sortie dans un dossier inexistant.
        from voice import concatener_voix
        with tempfile.TemporaryDirectory() as tmp:
            voix = os.path.join(tmp, "v.mp3")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error",
                 "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                 "-t", "1", voix], check=True)
            scenes = [{"id": 1, "voice_path": voix}]
            sortie = os.path.join(tmp, "cache", "voix_complete.mp3")
            concatener_voix(scenes, sortie)
            self.assertTrue(os.path.exists(sortie))
            self.assertTrue(os.path.exists(sortie + ".txt"))


if __name__ == "__main__":
    unittest.main()
