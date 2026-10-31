"""Régression reprise n° 2 : mix_audio sans musique ni ambiance."""
import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from mix import mix_audio

def _silence(path, duree=2):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono", "-t", str(duree),
                    path], check=True, capture_output=True)

class TestMix(unittest.TestCase):
    def test_voix_seule(self):
        """Cas exact de la reprise n° 2 : voix seule, pas de musique
        ni d'ambiance -> ne doit plus planter (asplit non connecté)."""
        with tempfile.TemporaryDirectory() as d:
            voix = os.path.join(d, "voix.mp3")
            _silence(voix)
            sortie = os.path.join(d, "audio_final.wav")
            mix_audio(voix, None, None, sortie)
            self.assertTrue(os.path.getsize(sortie) > 0)

    def test_voix_plus_musique(self):
        with tempfile.TemporaryDirectory() as d:
            voix = os.path.join(d, "voix.mp3")
            mus = os.path.join(d, "musique.mp3")
            _silence(voix); _silence(mus)
            sortie = os.path.join(d, "audio_final.wav")
            mix_audio(voix, mus, None, sortie)
            self.assertTrue(os.path.getsize(sortie) > 0)

    def test_voix_plus_ambiance(self):
        with tempfile.TemporaryDirectory() as d:
            voix = os.path.join(d, "voix.mp3")
            amb = os.path.join(d, "amb.mp3")
            _silence(voix); _silence(amb)
            sortie = os.path.join(d, "audio_final.wav")
            mix_audio(voix, None, amb, sortie)
            self.assertTrue(os.path.getsize(sortie) > 0)

if __name__ == "__main__":
    unittest.main()
