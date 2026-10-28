"""Assemblage final FFmpeg : concaténation des scènes, piste audio mixée,
sous-titres (SRT générés depuis la narration), sortie 1080p.
"""

import os
import subprocess


def _format_srt(t):
    """Secondes -> horodatage SRT (HH:MM:SS,mmm)."""
    h, reste = divmod(int(t), 3600)
    m, s = divmod(reste, 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generer_srt(scenes, sortie):
    """Un sous-titre par scène, calé sur les durées réelles de la voix."""
    lignes, t = [], 0.0
    for i, s in enumerate(scenes, 1):
        duree = s.get("duree_reelle_s", s.get("duree_estimee_s", 8))
        lignes.append(f"{i}\n{_format_srt(t)} --> {_format_srt(t + duree)}\n"
                      f"{s['narration']}\n")
        t += duree
    with open(sortie, "w", encoding="utf-8") as f:
        f.write("\n".join(lignes))
    return sortie


def assembler(segments, audio, sortie, sous_titres=None):
    """Concatène les segments vidéo et ajoute l'audio (+ sous-titres)."""
    liste = sortie + ".txt"
    with open(liste, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(f"file '{os.path.abspath(seg)}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", liste,
           "-i", audio, "-c:v", "libx264", "-preset", "fast",
           "-c:a", "aac", "-b:a", "192k", "-shortest"]
    if sous_titres:
        cmd += ["-vf", f"subtitles={sous_titres}:force_style='FontSize=22'"]
    cmd += ["-movflags", "+faststart", sortie]
    subprocess.run(cmd, check=True)
    return sortie
