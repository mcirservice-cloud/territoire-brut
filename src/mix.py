"""Mixage audio : voix prioritaire, musique et ambiance en ducking
(sidechain), normalisation finale loudnorm I=-16 (standard YouTube).

La voix est divisée en 3 (asplit) : une branche pilote le ducking de
la musique, une autre celui de l'ambiance, la troisième va au mix.
Les effets ponctuels (sfx) sont posés avec adelay à leur moment,
puis ajoutés au mix.
"""

from exec_utils import run as run_cmd

MUSIQUE_VOLUME = 0.35
AMBIANCE_VOLUME = 0.25
LOUDNORM = "I=-16:TP=-1.5:LRA=11"


def mix_audio(voix, musique, ambiance, sortie, sfx=None,
              musique_vol=MUSIQUE_VOLUME, ambiance_vol=AMBIANCE_VOLUME):
    """voix / musique / ambiance = chemins de pistes complètes.
    sfx = liste de (chemin, delai_s, volume)."""
    import os as _os
    if _os.path.dirname(sortie):
        _os.makedirs(_os.path.dirname(sortie), exist_ok=True)
    entrees = ["-i", voix]
    if musique:
        entrees += ["-i", musique]
    if ambiance:
        entrees += ["-i", ambiance]
    sfx = sfx or []
    for chemin, _delai, _vol in sfx:
        entrees += ["-i", chemin]

    # Indices : 0=voix, 1=musique, 2=ambiance, 3..=sfx
    # CORRECTIF reprise n° 2 (G013 ffmpeg asplit) : on ne divise la voix
    # qu'en autant de branches que nécessaire. Avant, asplit=3 était
    # systématique : sans musique ni ambiance, les sorties [v1] et [v2]
    # restaient non connectées et ffmpeg refusait ("unconnected output",
    # exit 234), ce qui tuait l'assemblage en 6 s.
    branches = []
    if musique:
        branches.append("musique")
    if ambiance:
        branches.append("ambiance")
    branches.append("mix")
    nb = len(branches)
    if nb == 1:
        filtres, voix_mix = [], "[0:a]"
    else:
        filtres = ["[0:a]asplit=%d%s" % (
            nb, "".join("[v%d]" % (i + 1) for i in range(nb)))]
        voix_mix = "[v%d]" % nb
    pistes = [voix_mix]
    idx = 1
    if musique:
        cle = "[v%d]" % (branches.index("musique") + 1)
        filtres.append(f"[{idx}:a]volume={musique_vol}[m]")
        filtres.append("[m]%ssidechaincompress=threshold=0.04:ratio=10"
                       ":attack=15:release=450[mduck]" % cle)
        pistes.append("[mduck]")
        idx += 1
    if ambiance:
        cle = "[v%d]" % (branches.index("ambiance") + 1)
        filtres.append(f"[{idx}:a]volume={ambiance_vol}[a]")
        filtres.append("[a]%ssidechaincompress=threshold=0.04:ratio=6"
                       ":attack=15:release=450[aduck]" % cle)
        pistes.append("[aduck]")
        idx += 1
    for chemin, delai, volume in sfx:
        ms = int(delai * 1000)
        filtres.append(f"[{idx}:a]adelay={ms}:all=1,volume={volume}[s{idx}]")
        pistes.append(f"[s{idx}]")
        idx += 1
    n = len(pistes)
    filtres.append(f"{''.join(pistes)}amix=inputs={n}:normalize=0,"
                   f"loudnorm={LOUDNORM}[out]")
    cmd = ["ffmpeg", "-y", *entrees, "-filter_complex",
           ";".join(filtres), "-map", "[out]", sortie]
    run_cmd(cmd, timeout=900)
    return sortie
