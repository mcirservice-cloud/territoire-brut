"""Assemblage final FFmpeg : concaténation des scènes, piste audio mixée,
sous-titres (SRT générés depuis la narration), sortie 1080p, puis
validation du rendu par échantillonnage de frames (voir valider_frames).
"""

import os
import tempfile

from exec_utils import check_output, run as run_cmd

SEUIL_NOIR = 8  # luminance moyenne < 8 = frame quasi noire
ECHANTILLONS_DEFAUT = 8
PROPORTION_MAX_NOIRES = 0.8  # au-delà, le rendu est considéré invalide


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
    """Concatène les segments vidéo et ajoute l'audio (+ sous-titres).

    Amélioration HD gratuite (filtres FFmpeg, sans GPU) appliquée au
    montage final : léger débruitage (hqdn3d), renforcement de la netteté
    (unsharp) et mise à l'échelle 1080p en lanczos.
    """
    liste = sortie + ".txt"
    with open(liste, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(f"file '{os.path.abspath(seg)}'\n")
    filtres = "hqdn3d,unsharp,scale=1920:1080:flags=lanczos"
    if sous_titres:
        filtres += f",subtitles={sous_titres}:force_style='FontSize=22'"
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", liste,
           "-i", audio, "-vf", filtres, "-c:v", "libx264", "-preset", "fast",
           "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-movflags", "+faststart", sortie]
    run_cmd(cmd, timeout=1800)
    return sortie


def luminance_moyenne_image(image):
    """Luminance moyenne (0-255) d'une image PIL, via le canal L."""
    gris = image.convert("L")
    pixels = list(gris.getdata())
    return sum(pixels) / len(pixels) if pixels else 0.0


def valider_frames(video, echantillons=ECHANTILLONS_DEFAUT,
                   seuil_noir=SEUIL_NOIR,
                   proportion_max_noires=PROPORTION_MAX_NOIRES):
    """Contrôle du RENDU (pas du contenu) : échantillonne N frames
    réparties sur la vidéo et vérifie qu'elles se décodent et ne sont
    pas quasi noires. Complète le fact-check, ne le remplace jamais.

    Renvoie un rapport dict {"ok": bool, "frames": [...], ...}.
    - ok=False si AUCUNE frame n'est décodable, ou si au moins
      proportion_max_noires des frames décodables sont quasi noires
      (seuil conservateur : une scène sombre reste très au-dessus).
    - Une frame isolée illisible ne bloque pas (avertissement seulement).
    L'appelant décide de la suite ; en cas d'échec, la vidéo reste dans
    output/ pour relecture humaine (politique « tout sauvegarder »).
    """
    from PIL import Image

    rapport = {"video": video, "frames": [], "ok": True,
               "raison": None}
    try:
        duree = float(check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video], timeout=60).strip())
    except Exception as e:
        rapport["ok"] = False
        rapport["raison"] = f"durée illisible via ffprobe ({e})"
        return rapport
    if duree <= 0:
        rapport["ok"] = False
        rapport["raison"] = "durée nulle"
        return rapport
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(echantillons):
            instant = duree * (i + 0.5) / echantillons
            image_png = os.path.join(tmp, f"frame_{i}.png")
            try:
                run_cmd(["ffmpeg", "-y", "-v", "error", "-ss", str(instant),
                         "-i", video, "-vframes", "1", image_png],
                        timeout=120)
                with Image.open(image_png) as img:
                    lum = luminance_moyenne_image(img)
                rapport["frames"].append(
                    {"instant_s": round(instant, 1),
                     "luminance": round(lum, 1),
                     "noire": lum < seuil_noir})
            except Exception as e:
                rapport["frames"].append(
                    {"instant_s": round(instant, 1),
                     "erreur": f"{type(e).__name__}"})
    decodables = [f for f in rapport["frames"] if "luminance" in f]
    if not decodables:
        rapport["ok"] = False
        rapport["raison"] = "aucune frame décodable"
        return rapport
    noires = sum(1 for f in decodables if f["noire"])
    rapport["frames_noires"] = noires
    rapport["frames_decodables"] = len(decodables)
    if noires / len(decodables) >= proportion_max_noires:
        rapport["ok"] = False
        rapport["raison"] = (f"{noires}/{len(decodables)} frames quasi "
                             "noires — rendu probablement invalide")
    return rapport
