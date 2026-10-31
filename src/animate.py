"""Animation des images fixes (Ken Burns) et adaptation des clips vidéo.

Traitement « pro » (Sam, 2026-10-31) : chaque segment, vidéo ou
photo, reçoit le même habillage — étalonnage léger (contraste,
saturation), vignettage discret, netteté, et fondu d'entrée/sortie
(0,4 s) pour que les coupures entre scènes ne soient plus sèches.
Les clips vidéo réels sont pris au MILIEU du fichier source (le
début contient souvent un titre ou un plan d'accroche hors sujet),
et bouclés seulement s'ils sont plus courts que la scène.

Modes : zoom avant / zoom arrière / travelling gauche / droite.
La parallaxe (2.5D) et les clips IA sont hors périmètre du runner
GitHub Actions (GPU) : l'animation "parallaxe" retombe sur un zoom,
"clip_ia" doit être fourni par Colab/Kaggle (voir README).
"""

from exec_utils import check_output, run as run_cmd

FPS = 25
LARGEUR = 1920
HAUTEUR = 1080
# Étalonnage + vignette + netteté, identiques pour vidéo et photo :
# le rendu reste homogène d'une scène à l'autre, même quand les
# sources (Wikimedia, Openverse) ont des colorimétries différentes.
HABILLAGE = "eq=contrast=1.06:saturation=1.12:brightness=0.02,vignette=PI/4,unsharp=5:5:0.3"


def _fondus(duree):
    """Fondus noir entrée/sortie (0,4 s), bornés à la durée."""
    d = min(0.4, duree / 4) if duree > 0 else 0.4
    return f"fade=t=in:st=0:d={d},fade=t=out:st={max(0.0, duree - d)}:d={d}"


def ken_burns(image, sortie, duree, mode="zoom_in", fps=FPS):
    """Anime une photo en vidéo 1080p (zoompan FFmpeg)."""
    nb_images = int(duree * fps)
    pas_zoom = 0.35 / nb_images
    if mode == "zoom_in":
        z = f"min(zoom+{pas_zoom:.6f},1.35)"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mode == "zoom_out":
        z = f"if(eq(on,0),1.35,max(zoom-{pas_zoom:.6f},1.0))"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mode == "travelling_gauche":
        z, x, y = "1.25", f"(iw-iw/zoom)*(1-on/{nb_images})", "ih/2-(ih/zoom/2)"
    else:  # travelling_droite
        z, x, y = "1.25", f"(iw-iw/zoom)*(on/{nb_images})", "ih/2-(ih/zoom/2)"
    # Vitesse run #13 : l'ancien suréchantillonnage 3840 (2x la sortie)
    # et le preset « fast » coûtaient ~2 min par scène. 2880 donne
    # encore une marge de zoom de 1,5x pour une sortie 1920 (zoom max
    # 1,35x), et « veryfast » divise le temps d'encodage sans changer
    # la résolution de sortie.
    vf = (f"scale=1920:-2,zoompan=z='{z}':x='{x}':y='{y}':d={nb_images}:"
          f"s={LARGEUR}x{HAUTEUR}:fps={fps},{HABILLAGE},{_fondus(duree)},"
          f"format=yuv420p")
    run_cmd(["ffmpeg", "-y", "-loop", "1", "-i", image, "-vf", vf,
             "-t", str(duree), "-c:v", "libx264", "-preset", "ultrafast",
             sortie], timeout=600)
    return sortie


def duree_clip(clip):
    """Durée d'un clip en secondes (ffprobe), ou None si illisible."""
    try:
        return float(check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", clip], timeout=60).strip())
    except Exception:
        return None


def fit_clip(clip, sortie, duree):
    """Adapte un clip vidéo : recadrage 1080p, habillage pro, fondus.
    Le plan est pris au MILIEU du fichier source quand il est assez
    long (le début contient souvent un titre ou un plan hors sujet) ;
    un clip plus court que la scène est bouclé comme avant."""
    vf = (f"scale={LARGEUR}:{HAUTEUR}:force_original_aspect_ratio=increase,"
          f"crop={LARGEUR}:{HAUTEUR},fps={FPS},{HABILLAGE},{_fondus(duree)},"
          f"format=yuv420p")
    duree_source = duree_clip(clip)
    if duree_source is not None and duree_source >= duree:
        depart = max(0.0, (duree_source - duree) / 2)
        print(f"[animation] Clip réel {duree_source:.1f}s, plan pris à "
              f"{depart:.1f}s pour {duree:.1f}s.")
        run_cmd(["ffmpeg", "-y", "-ss", str(depart), "-i", clip,
                 "-t", str(duree), "-vf", vf, "-an",
                 "-c:v", "libx264", "-preset", "fast", sortie],
                timeout=600)
    else:
        run_cmd(["ffmpeg", "-y", "-stream_loop", "-1", "-i", clip,
                 "-t", str(duree), "-vf", vf, "-an",
                 "-c:v", "libx264", "-preset", "fast", sortie], timeout=600)
    return sortie


def animer_scene(visuel, type_visuel, sortie, duree, animation):
    """Rend une scène en vidéo muette de la bonne durée.
    - video : recadrage / coupe du clip.
    - photo : Ken Burns selon l'animation demandée (parallaxe -> zoom avant,
      clip_ia -> zoom avant en attendant le clip généré sur Colab/Kaggle)."""
    if type_visuel == "video":
        return fit_clip(visuel, sortie, duree)
    mode = {"ken_burns_zoom_in": "zoom_in",
            "ken_burns_zoom_out": "zoom_out",
            "travelling_gauche": "travelling_gauche",
            "travelling_droite": "travelling_droite"}.get(animation, "zoom_in")
    if animation in ("parallaxe", "clip_ia"):
        print(f"[animation] {animation} non disponible sur le runner : "
              "repli sur un zoom avant.")
    return ken_burns(visuel, sortie, duree, mode)
