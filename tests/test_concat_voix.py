"""Régression reprise n° 3 (G009) : la piste voix concaténée doit
inclure les pauses de 0,4 s, sinon elle est plus courte que la vidéo
et le montage -shortest coupe la fin."""
import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from voice import concatener_voix, duree_audio, PAUSE_ENTRE_SCENES_S

def _voix(path, duree=2):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono", "-t", str(duree),
                    "-codec:a", "libmp3lame", "-q:a", "4", path],
                   check=True, capture_output=True)

class TestConcatVoix(unittest.TestCase):
    def test_pauses_incluses(self):
        with tempfile.TemporaryDirectory() as d:
            scenes = []
            for i in range(3):
                p = os.path.join(d, f"scene_{i}.mp3")
                _voix(p, duree=2)
                scenes.append({"id": i + 1, "voice_path": p,
                               "duree_reelle_s": round(2 + PAUSE_ENTRE_SCENES_S, 2)})
            sortie = os.path.join(d, "cache", "voix_complete.mp3")
            concatener_voix(scenes, sortie)
            attendu = sum(s["duree_reelle_s"] for s in scenes)
            obtenu = duree_audio(sortie)
            self.assertAlmostEqual(obtenu, attendu, delta=0.5)

if __name__ == "__main__":
    unittest.main()
