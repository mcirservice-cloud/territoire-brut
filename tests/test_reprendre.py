"""Tests Reprendre : rien ne se perd, on ne refait que le manquant."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from reprendre import etat_reprise, dossiers_shards_valides


def _shard_complet(tmp, index, ids):
    dossier = os.path.join(tmp, f"shard-{index}")
    os.makedirs(dossier, exist_ok=True)
    scenes = []
    for sid in ids:
        seg = os.path.join(dossier, f"scene_{sid:03d}.mp4")
        voix = os.path.join(dossier, f"scene_{sid:03d}.mp3")
        open(seg, "wb").write(b"x")
        open(voix, "wb").write(b"x")
        scenes.append({"id": sid, "segment": seg, "voice_path": voix})
    with open(os.path.join(dossier, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump({"shard": index, "scenes": scenes}, f)


class TestReprendre(unittest.TestCase):
    def test_etat_reprise(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "script.json"), "w").write("{}")
            open(os.path.join(tmp, "sujet.json"), "w").write("{}")
            _shard_complet(tmp, 0, [1, 11])
            _shard_complet(tmp, 1, [2])
            os.makedirs(os.path.join(tmp, "shard-2"), exist_ok=True)
            etat = etat_reprise(tmp, shard_count=3)
            self.assertTrue(etat["script"])
            self.assertTrue(etat["sujet"])
            self.assertEqual(etat["shards_termines"], [0, 1])
            self.assertEqual(etat["shards_manquants"], [2])
            self.assertEqual(etat["scenes_faites"], [1, 2, 11])

    def test_etat_sans_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            etat = etat_reprise(tmp, shard_count=2)
            self.assertFalse(etat["script"])
            self.assertEqual(etat["shards_manquants"], [0, 1])

    def test_dossiers_valides_ignore_sans_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            _shard_complet(tmp, 0, [1])
            os.makedirs(os.path.join(tmp, "shard-9"), exist_ok=True)
            valides, ignores = dossiers_shards_valides(tmp)
            self.assertEqual(len(valides), 1)
            self.assertTrue(valides[0].endswith("shard-0"))
            self.assertEqual(len(ignores), 1)


if __name__ == "__main__":
    unittest.main()
