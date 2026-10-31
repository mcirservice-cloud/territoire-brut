"""Régression reprise n° 6 : un 403 sur thumbnails.set ne doit pas
tuer la livraison, la vidéo étant déjà envoyée."""
import os, sys, types, unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
# googleapiclient n'est pas installé sur cette VM : on le simule,
# comme le fait déjà test_smoke_v2 pour les modules Google.
class HttpError(Exception):
    def __init__(self, resp, content):
        super().__init__(content)
        self.resp = resp
pkg = types.ModuleType("googleapiclient")
err = types.ModuleType("googleapiclient.errors"); err.HttpError = HttpError
httpm = types.ModuleType("googleapiclient.http")
httpm.MediaFileUpload = MagicMock
disc = types.ModuleType("googleapiclient.discovery")
disc.build = MagicMock()
pkg.errors, pkg.http, pkg.discovery = err, httpm, disc
sys.modules.update({"googleapiclient": pkg, "googleapiclient.errors": err,
                    "googleapiclient.http": httpm,
                    "googleapiclient.discovery": disc})
oauth = types.ModuleType("google.oauth2.credentials")
oauth.Credentials = MagicMock
sys.modules["google.oauth2.credentials"] = oauth
import upload

class Resp:
    status = 403
    reason = "Forbidden"

class TestMiniature(unittest.TestCase):
    def test_miniature_403_non_fatale(self):
        yt = MagicMock()
        req = MagicMock()
        req.next_chunk.return_value = (None, {"id": "VIDEO123"})
        yt.videos.return_value.insert.return_value = req
        yt.thumbnails.return_value.set.return_value.execute.side_effect = \
            HttpError(Resp(), b"forbidden")
        with patch.object(upload, "build", return_value=yt), \
             patch.object(upload, "youtube", return_value=yt), \
             patch("os.path.exists", return_value=True):
            # upload() appelle youtube() en interne via build ; on
            # contourne en patchant directement la fabrique utilisée
            with patch.object(upload, "build", return_value=yt):
                vid = upload.upload("video.mp4", "mini.jpg",
                                    {"titre": "T", "description": "D"})
        self.assertEqual(vid, "VIDEO123")

if __name__ == "__main__":
    unittest.main()
