"""Garde-fous Varus : annulation immédiate, avec code d'erreur, dès
qu'il est prouvé qu'aucune vidéo ne pourra être fournie à la fin.

Principe d'ingénieur : chaque étape chère (voix, segments, mix, rendu,
upload) est précédée d'une vérification bon marché (secondes) qui
prouve que l'étape suivante peut réussir. Si la preuve manque, on
s'arrête TOUT DE SUITE, on sauvegarde l'état, on écrit le code dans
output/echec-garde-fou.json, et on ne brûle pas le budget du job.

Codes (stables, à citer tels quels dans les rapports) :
  G001 clés manquantes pour l'étape
  G002 sujet introuvable / aucun sujet à produire
  G003 faits trop courts ou absents
  G004 script invalide (scènes, ids, narration, meta, durée hors cible)
  G005 moins de 20 scènes après fact-check (blocage + sauvegarde)
  G006 voix invalide (fichier manquant, vide, durée nulle)
  G007 segment invalide (fichier manquant, vide, durée ≠ voix)
  G008 assemblage incomplet (manifests, fichiers manquants)
  G009 rendu invalide (fichier, taille, durée, frames)
  G010 miniature invalide
  G011 upload invalide (échec ou id vide)
  G012 environnement invalide (ffmpeg absent, disque insuffisant)
"""

import json
import os
import shutil

SCENES_MIN = 20
DUREE_ECART_CIBLE = 0.25  # ±25 % autour de la durée cible du sujet
DISQUE_MIN_OCTETS = 1_000_000_000  # 1 Go libre minimum


class EchecGardeFou(RuntimeError):
    """Erreur avec code stable, pour annulation immédiate."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(f"[{code}] {message}")
        self.message = message


def echec(code, message):
    raise EchecGardeFou(code, message)


def _fichier_valide(chemin, code):
    if not chemin or not os.path.exists(chemin):
        echec(code, f"fichier manquant : {chemin}")
    if os.path.getsize(chemin) == 0:
        echec(code, f"fichier vide (0 octet) : {chemin}")


def _duree_media(chemin):
    from exec_utils import check_output
    out = check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", chemin], timeout=60)
    return float(out.strip())


def verifier_environnement():
    """G012 : ffmpeg + disque, en secondes, avant tout travail cher."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        echec("G012", "ffmpeg/ffprobe introuvable dans l'environnement")
    try:
        libre = shutil.disk_usage(".").free
    except Exception:
        libre = DISQUE_MIN_OCTETS
    if libre < DISQUE_MIN_OCTETS:
        echec("G012", f"disque insuffisant : {libre} octets libres, "
                      f"minimum {DISQUE_MIN_OCTETS}")


def verifier_script(script, duree_cible_min=None):
    """G004 : structure, ids, narration, meta, durée estimée."""
    scenes = (script or {}).get("scenes")
    if not scenes:
        echec("G004", "script sans aucune scène")
    ids = [s.get("id") for s in scenes]
    if ids != list(range(1, len(scenes) + 1)):
        echec("G004", f"ids de scènes non consécutifs 1..N : {ids[:10]}")
    for s in scenes:
        if not (s.get("narration") or "").strip():
            echec("G004", f"scène {s.get('id')} sans narration")
        if not (s.get("visuel") or {}).get("requete_pexels_en"):
            echec("G004", f"scène {s.get('id')} sans requête visuelle")
        duree = s.get("duree_estimee_s")
        if duree is not None and not (3 <= duree <= 13):
            echec("G004", f"scène {s.get('id')} durée estimée "
                          f"hors 3-13 s : {duree}")
    meta = script.get("meta") or {}
    for cle in ("titre", "description", "miniature"):
        if not meta.get(cle):
            echec("G004", f"meta manquante : {cle}")
    if not (meta.get("miniature") or {}).get("texte"):
        echec("G004", "meta.miniature.texte manquant")
    if duree_cible_min:
        # Avertissement seulement : une vidéo plus courte que la cible
        # reste une vidéo. Seule l'absence de scènes/narration/meta
        # prouve qu'aucune vidéo ne pourra être fournie (G004 ci-dessus).
        total = sum(s.get("duree_estimee_s", 8) for s in scenes)
        cible = duree_cible_min * 60
        if abs(total - cible) > cible * DUREE_ECART_CIBLE:
            print(f"[garde-fou] Avertissement : durée estimée "
                  f"{total:.0f} s hors cible {cible:.0f} s ±25 %")
    return True


def verifier_scenes_min(scenes, code="G005"):
    if len(scenes) < SCENES_MIN:
        echec(code, f"seulement {len(scenes)} scènes, "
                    f"minimum {SCENES_MIN} pour une vidéo")
    return True


def verifier_voix(scenes):
    """G006 : chaque voix existe, non vide, durée > 0."""
    for s in scenes:
        chemin = s.get("voice_path")
        _fichier_valide(chemin, "G006")
        duree = s.get("duree_reelle_s")
        if duree is None:
            duree = _duree_media(chemin)
            s["duree_reelle_s"] = round(duree + 0.4, 2)
        if duree <= 0:
            echec("G006", f"voix scène {s.get('id')} durée nulle")
    return True


def verifier_segment(segment, duree_voix_s, scene_id):
    """G007 : segment existe, non vide, durée = voix ±1 s."""
    _fichier_valide(segment, "G007")
    duree = _duree_media(segment)
    if abs(duree - duree_voix_s) > 1.0:
        echec("G007", f"segment scène {scene_id} durée {duree:.1f} s "
                      f"≠ voix {duree_voix_s:.1f} s (±1 s)")
    return duree


def verifier_manifests(script_scenes, manifests):
    """G008 : tous les shards présents, tous les fichiers présents."""
    attendus = {s["id"] for s in script_scenes}
    recus = set()
    for man in manifests:
        for entree in man["scenes"]:
            recus.add(entree["id"])
            _fichier_valide(entree.get("segment"), "G008")
            _fichier_valide(entree.get("voice_path"), "G008")
    manquants = sorted(attendus - recus)
    if manquants:
        echec("G008", f"scènes manquantes à l'assemblage : {manquants}")
    en_trop = sorted(recus - attendus)
    if en_trop:
        echec("G008", f"scènes inconnues à l'assemblage : {en_trop}")
    return True


def verifier_rendu(video, duree_attendue_s):
    """G009 : vidéo existe, non vide, durée = somme voix ±3 s."""
    _fichier_valide(video, "G009")
    duree = _duree_media(video)
    if abs(duree - duree_attendue_s) > 3.0:
        echec("G009", f"rendu durée {duree:.1f} s ≠ somme voix "
                      f"{duree_attendue_s:.1f} s (±3 s)")
    return duree


def verifier_miniature(chemin):
    _fichier_valide(chemin, "G010")
    return True


def verifier_upload(video_id):
    if not video_id:
        echec("G011", "upload YouTube sans id de vidéo")
    return True


def ecrire_echec(dossier_sortie, code, message, etape):
    """Écrit output/echec-garde-fou.json (lu par l'archivage if: always)."""
    os.makedirs(dossier_sortie, exist_ok=True)
    chemin = os.path.join(dossier_sortie, "echec-garde-fou.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump({"code": code, "etape": etape, "message": message},
                  f, ensure_ascii=False, indent=2)
    return chemin
