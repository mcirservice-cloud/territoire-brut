"""Tests ALPHA/BÉTA : il y a obligatoirement une vidéo à la fin.

Le BÉTA ne crée aucune affirmation : narration = phrases des faits.
Le montage final rapide ne ré-encode pas : collage des segments
déjà montés en parallèle."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from plan_b import (construire_script_secours, completer_script,
                    NB_SCENES_CIBLE)
from script_gen import validate
from gardefous import verifier_script

FAITS = (
    "Le guépard est le mammifère terrestre le plus rapide du monde. "
    "Il peut atteindre 110 kilomètres par heure en sprint. "
    "Son corps élancé et sa colonne vertébrale flexible lui permettent "
    "de grandes foulées. Il vit principalement dans les savanes "
    "d'Afrique. Il chasse surtout les petites antilopes. "
    "Sa vitesse ne dure que quelques dizaines de secondes. "
    "Après un sprint, il doit se reposer pour récupérer. "
    "Les jeunes guépards restent longtemps avec leur mère. "
    "Le guépard est une espèce menacée par la perte d'habitat. "
    "Il possède des griffes non rétractiles, uniques chez les félins. "
    "Sa vision est excellente, même à grande distance. "
    "Il chasse de jour, contrairement à la plupart des félins. "
    "Son pelage tacheté lui offre un camouflage dans les herbes hautes. "
    "La femelle élève seule ses petits après la naissance. "
    "Les guépards évitent les lions et les hyènes, plus puissants. "
    "Ils boivent peu d'eau et tirent l'humidité de leurs proies. "
    "Leur queue longue sert de gouvernail pendant la course. "
    "Les guépards peuvent vivre une dizaine d'années dans la nature. "
    "Ils marquent leur territoire avec des odeurs et des griffades. "
    "La chasse réussit environ une fois sur deux chez le guépard. "
    "Les petits apprennent à chasser en jouant avec leur mère. "
    "Le guépard dort souvent dans les arbres ou les herbes hautes. "
    "Son museau court améliore sa respiration pendant l'effort. "
    "Les populations de guépards ont fortement diminué en un siècle. "
)

SUJET = {"nom": "Guépard", "nom_latin": "Acinonyx jubatus",
         "angle": "survie"}


class TestPlanB(unittest.TestCase):
    def test_script_secours_valide(self):
        script = construire_script_secours(FAITS, SUJET)
        self.assertEqual(len(script["scenes"]), NB_SCENES_CIBLE)
        validate(script)
        self.assertTrue(verifier_script(script))
        self.assertEqual(script["meta"]["chemin"], "beta")
        # Aucune invention : chaque narration vient des faits.
        for s in script["scenes"]:
            self.assertIn(s["narration"][:40], FAITS)

    def test_completer_remplit_a_24(self):
        script = {"meta": {"titre": "T", "description": "D",
                           "miniature": {"texte": "X"}},
                  "scenes": [
                      {"id": 1, "narration": "Phrase gardée.",
                       "visuel": {"requete_pexels_en": "cheetah"},
                       "duree_estimee_s": 8,
                       "animation": "ken_burns_zoom_in"},
                      {"id": 2, "narration": "Phrase signalée.",
                       "visuel": {"requete_pexels_en": "cheetah"},
                       "duree_estimee_s": 8, "a_verifier": True,
                       "animation": "ken_burns_zoom_out"}]}
        complet = completer_script(script, FAITS, SUJET)
        self.assertEqual(len(complet["scenes"]), NB_SCENES_CIBLE)
        self.assertEqual(complet["scenes"][0]["narration"],
                         "Phrase gardée.")
        self.assertNotIn("Phrase signalée.",
                         [s["narration"] for s in complet["scenes"]])
        validate(complet)
        self.assertTrue(verifier_script(complet))

    def test_assembler_rapide_colle_sans_reencoder(self):
        import subprocess
        import tempfile
        from render import assembler_rapide
        with tempfile.TemporaryDirectory() as tmp:
            segs = []
            for i in range(2):
                seg = os.path.join(tmp, f"seg{i}.mp4")
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error",
                     "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=1",
                     "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                     "-t", "1", "-c:v", "libx264", "-c:a", "aac", seg],
                    check=True)
                segs.append(seg)
            audio = os.path.join(tmp, "audio.wav")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error",
                 "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                 "-t", "2", audio], check=True)
            sortie = os.path.join(tmp, "final.mp4")
            assembler_rapide(segs, audio, sortie)
            self.assertTrue(os.path.exists(sortie))
            self.assertGreater(os.path.getsize(sortie), 0)


if __name__ == "__main__":
    unittest.main()
