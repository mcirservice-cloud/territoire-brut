"""Montage pro (Sam, 2026-10-31) : vraie vidéo d'abord sur toutes
les formes, plan pris au milieu du clip, habillage + fondus."""
import os, sys, unittest
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import assets, animate


class TestVideoDabord(unittest.TestCase):
    def test_video_forme_simple_bat_photo_forme_complete(self):
        # Ancien ordre : la photo de la forme complète gagnait. Nouvel
        # ordre : la vidéo trouvée sous « cheetah » doit gagner.
        with patch.object(assets, "wikimedia_video",
                          side_effect=lambda req: None if req != "cheetah"
                          else "clip.mp4"), \
             patch.object(assets, "pixabay_video", return_value=None), \
             patch.object(assets, "pexels_video", return_value=None), \
             patch.object(assets, "openverse_photo",
                          return_value="photo.jpg"):
            chemin, type_visuel = assets.trouver_visuel(
                "cheetah running fast", budget_s=30)
        self.assertEqual((chemin, type_visuel), ("clip.mp4", "video"))

    def test_photo_si_aucune_video(self):
        with patch.object(assets, "wikimedia_video", return_value=None), \
             patch.object(assets, "pixabay_video", return_value=None), \
             patch.object(assets, "pexels_video", return_value=None), \
             patch.object(assets, "openverse_photo",
                          return_value="photo.jpg"):
            chemin, type_visuel = assets.trouver_visuel(
                "cheetah running fast", budget_s=30)
        self.assertEqual((chemin, type_visuel), ("photo.jpg", "photo"))


class TestHabillage(unittest.TestCase):
    def test_fit_clip_plan_au_milieu_et_fondus(self):
        capture = {}
        def faux_run(cmd, timeout):
            capture["cmd"] = cmd
        with patch.object(animate, "run_cmd", side_effect=faux_run), \
             patch.object(animate, "duree_clip", return_value=60.0):
            animate.fit_clip("clip.mp4", "out.mp4", 10.0)
        cmd = capture["cmd"]
        self.assertIn("-ss", cmd)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "25.0")  # milieu
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("vignette", vf)
        self.assertIn("fade=t=in", vf)
        self.assertIn("fade=t=out", vf)

    def test_fit_clip_court_boucle(self):
        capture = {}
        def faux_run(cmd, timeout):
            capture["cmd"] = cmd
        with patch.object(animate, "run_cmd", side_effect=faux_run), \
             patch.object(animate, "duree_clip", return_value=4.0):
            animate.fit_clip("clip.mp4", "out.mp4", 10.0)
        self.assertIn("-stream_loop", capture["cmd"])

    def test_ken_burns_habillage(self):
        capture = {}
        def faux_run(cmd, timeout):
            capture["cmd"] = cmd
        with patch.object(animate, "run_cmd", side_effect=faux_run):
            animate.ken_burns("photo.jpg", "out.mp4", 8.0, "zoom_in")
        vf = capture["cmd"][capture["cmd"].index("-vf") + 1]
        self.assertIn("vignette", vf)
        self.assertIn("fade=t=in", vf)


if __name__ == "__main__":
    unittest.main()
