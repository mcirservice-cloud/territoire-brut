"""Voix off par scène avec edge-tts (fr-FR-HenriNeural).

La durée réelle de la voix (mesurée par ffprobe) fixe la durée de
chaque scène : tout reste synchronisé automatiquement.
"""

import asyncio
import os
import subprocess

import edge_tts

VOIX = "fr-FR-HenriNeural"
RATE = "-5%"
PITCH = "-2Hz"
PAUSE_ENTRE_SCENES_S = 0.4


async def _tts(texte, sortie):
    await edge_tts.Communicate(texte, VOIX, rate=RATE, pitch=PITCH).save(sortie)


def duree_audio(chemin):
    """Durée réelle d'un fichier audio via ffprobe (secondes)."""
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", chemin])
    return float(out.strip())


def make_voice(scenes, dossier="cache/voice"):
    """Génère un MP3 par scène, ajoute voice_path et duree_reelle_s."""
    os.makedirs(dossier, exist_ok=True)
    for s in scenes:
        chemin = os.path.join(dossier, f"scene_{s['id']:03d}.mp3")
        if not os.path.exists(chemin):
            asyncio.run(_tts(s["narration"], chemin))
        s["voice_path"] = chemin
        s["duree_reelle_s"] = round(duree_audio(chemin) + PAUSE_ENTRE_SCENES_S, 2)
    return scenes


def concatener_voix(scenes, sortie):
    """Concatène toutes les voix en une seule piste (fichier liste FFmpeg)."""
    liste = sortie + ".txt"
    with open(liste, "w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"file '{os.path.abspath(s['voice_path'])}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", liste, "-c", "copy", sortie], check=True)
    return sortie
