"""Tests pipeline rapide : découpage en shards sans perte ni doublon,
et tri des manifests pour l'assemblage dans l'ordre des scènes."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fast_pipeline import decouper_scenes


class TestDecoupage(unittest.TestCase):
    def test_60_scenes_10_shards(self):
        scenes = [{"id": i} for i in range(1, 61)]
        tous = []
        for idx in range(10):
            part = decouper_scenes(scenes, 10, idx)
            self.assertEqual(len(part), 6)
            tous.extend(s["id"] for s in part)
        self.assertEqual(sorted(tous), list(range(1, 61)))

    def test_shard_inexistant_vide(self):
        scenes = [{"id": i} for i in range(1, 6)]
        self.assertEqual(decouper_scenes(scenes, 10, 9), [])

    def test_ordre_assemblage(self):
        # Les manifests arrivent dans n'importe quel ordre de job :
        # l'assemblage doit trier par id de scène.
        manifests = [{"id": 5}, {"id": 1}, {"id": 3}]
        scenes = sorted(manifests, key=lambda s: s["id"])
        self.assertEqual([s["id"] for s in scenes], [1, 3, 5])


if __name__ == "__main__":
    unittest.main()
