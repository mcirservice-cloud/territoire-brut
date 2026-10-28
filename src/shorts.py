"""Extraction des Shorts : 3 extraits de 25 à 45 s par vidéo,
recadrés en vertical 1080x1920 (9:16), à partir des scènes listées
dans le script (meta "shorts" : ids_scenes + accroche).
"""

import os
import subprocess

LARGEUR, HAUTEUR = 1080, 1920


def _duree(fichier):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", fichier])
    return float(out.strip())


def extraire_shorts(video, script, dossier="output/shorts"):
    """Renvoie la liste des chemins des Shorts générés."""
    os.makedirs(dossier, exist_ok=True)
    scenes = {s["id"]: s for s in script["scenes"]}
    # Durées cumulées pour retrouver les bornes de chaque scène
    debuts, t = {}, 0.0
    for s in script["scenes"]:
        debuts[s["id"]] = t
        t += s.get("duree_reelle_s", s.get("duree_estimee_s", 8))

    sorties = []
    for i, short in enumerate(script.get("shorts", [])[:3], 1):
        ids = short.get("ids_scenes", [])
        if not ids:
            continue
        debut = debuts[ids[0]]
        fin = debuts[ids[-1]] + scenes[ids[-1]].get(
            "duree_reelle_s", scenes[ids[-1]].get("duree_estimee_s", 8))
        duree = min(fin - debut, 45)
        if duree < 25:
            continue
        sortie = os.path.join(dossier, f"short_{i}.mp4")
        vf = (f"scale={LARGEUR}:{HAUTEUR}:force_original_aspect_ratio=increase,"
              f"crop={LARGEUR}:{HAUTEUR},fps=25,format=yuv420p")
        subprocess.run(["ffmpeg", "-y", "-ss", str(debut), "-i", video,
                        "-t", str(duree), "-vf", vf,
                        "-c:v", "libx264", "-preset", "fast",
                        "-c:a", "aac", sortie], check=True)
        sorties.append(sortie)
    return sorties
