"""Mixage audio : voix prioritaire, musique et ambiance en ducking
(sidechain), normalisation finale loudnorm I=-16 (standard YouTube).

La voix est divisée en 3 (asplit) : une branche pilote le ducking de
la musique, une autre celui de l'ambiance, la troisième va au mix.
Les effets ponctuels (sfx) sont posés avec adelay à leur moment,
puis ajoutés au mix.
"""

import subprocess

MUSIQUE_VOLUME = 0.35
AMBIANCE_VOLUME = 0.25
LOUDNORM = "I=-16:TP=-1.5:LRA=11"


def mix_audio(voix, musique, ambiance, sortie, sfx=None,
              musique_vol=MUSIQUE_VOLUME, ambiance_vol=AMBIANCE_VOLUME):
    """voix / musique / ambiance = chemins de pistes complètes.
    sfx = liste de (chemin, delai_s, volume)."""
    entrees = ["-i", voix]
    if musique:
        entrees += ["-i", musique]
    if ambiance:
        entrees += ["-i", ambiance]
    sfx = sfx or []
    for chemin, _delai, _vol in sfx:
        entrees += ["-i", chemin]

    # Indices : 0=voix, 1=musique, 2=ambiance, 3..=sfx
    filtres = ["[0:a]asplit=3[v1][v2][v3]"]
    pistes = ["[v3]"]
    idx = 1
    if musique:
        filtres.append(f"[{idx}:a]volume={musique_vol}[m]")
        filtres.append("[m][v1]sidechaincompress=threshold=0.04:ratio=10"
                       ":attack=15:release=450[mduck]")
        pistes.append("[mduck]")
        idx += 1
    if ambiance:
        filtres.append(f"[{idx}:a]volume={ambiance_vol}[a]")
        filtres.append("[a][v2]sidechaincompress=threshold=0.04:ratio=6"
                       ":attack=15:release=450[aduck]")
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
    subprocess.run(cmd, check=True)
    return sortie
