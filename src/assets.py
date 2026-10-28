"""Images, clips et sons : Pixabay EN PRIORITÉ, Pexels en secours,
Freesound filtré CC0, Xeno-canto sans clé.

- Pixabay d'abord : les nouvelles clés Pexels sont en pause.
- 1 requête par scène maximum.
- Cache local + journal d'usage (cache/usage_log.json) : un clip déjà
  utilisé dans une vidéo récente n'est pas réutilisé.
"""

import hashlib
import json
import os

import requests

TIMEOUT = 60
JOURNAL = "cache/usage_log.json"
INTERDIT_NB_VIDEOS = 5


def _telecharger(url, chemin):
    if os.path.exists(chemin):
        return chemin
    r = requests.get(url, timeout=TIMEOUT,
                     headers={"User-Agent": "TerritoireBrut/1.0"})
    r.raise_for_status()
    with open(chemin, "wb") as f:
        f.write(r.content)
    return chemin


def _journal_lire():
    try:
        with open(JOURNAL, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"clips": []}


def _journal_enregistrer(identifiant):
    journal = _journal_lire()
    journal["clips"].append(identifiant)
    journal["clips"] = journal["clips"][-200:]
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    with open(JOURNAL, "w", encoding="utf-8") as f:
        json.dump(journal, f)


def deja_utilise(identifiant, nb_videos=INTERDIT_NB_VIDEOS):
    """Vrai si le clip a servi dans l'une des nb_videos dernières vidéos."""
    journal = _journal_lire()
    return identifiant in journal["clips"][-nb_videos:]


# --- Vidéos et photos ------------------------------------------------

def pixabay_video(requete, dossier="cache/clips"):
    """Clip vidéo Pixabay (clé PIXABAY_KEY). Renvoie le chemin ou None."""
    cle = os.environ.get("PIXABAY_KEY")
    if not cle:
        return None
    os.makedirs(dossier, exist_ok=True)
    r = requests.get("https://pixabay.com/api/videos/",
                     params={"key": cle, "q": requete, "per_page": 5,
                             "video_type": "film", "min_width": 1280},
                     timeout=TIMEOUT)
    r.raise_for_status()
    for video in r.json().get("hits", []):
        fichiers = video.get("videos", {})
        candidat = (fichiers.get("large") or fichiers.get("medium")
                    or fichiers.get("small") or {}).get("url")
        if not candidat:
            continue
        identifiant = f"pixabay:{video.get('id')}"
        if deja_utilise(identifiant):
            continue
        chemin = os.path.join(
            dossier, hashlib.md5(candidat.encode()).hexdigest() + ".mp4")
        _telecharger(candidat, chemin)
        _journal_enregistrer(identifiant)
        return chemin
    return None


def pixabay_photo(requete, dossier="cache/photos"):
    """Photo Pixabay (clé PIXABAY_KEY). Renvoie le chemin ou None."""
    cle = os.environ.get("PIXABAY_KEY")
    if not cle:
        return None
    os.makedirs(dossier, exist_ok=True)
    r = requests.get("https://pixabay.com/api/",
                     params={"key": cle, "q": requete, "per_page": 5,
                             "image_type": "photo", "min_width": 1280},
                     timeout=TIMEOUT)
    r.raise_for_status()
    for photo in r.json().get("hits", []):
        url = photo.get("largeImageURL")
        if not url:
            continue
        identifiant = f"pixabay:{photo.get('id')}"
        if deja_utilise(identifiant):
            continue
        chemin = os.path.join(dossier, f"{photo['id']}.jpg")
        _telecharger(url, chemin)
        _journal_enregistrer(identifiant)
        return chemin
    return None


def pexels_video(requete, dossier="cache/clips"):
    """Clip vidéo Pexels (clé PEXELS_KEY, secours). Renvoie le chemin ou None."""
    cle = os.environ.get("PEXELS_KEY")
    if not cle:
        return None
    os.makedirs(dossier, exist_ok=True)
    r = requests.get("https://api.pexels.com/videos/search",
                     headers={"Authorization": cle},
                     params={"query": requete, "orientation": "landscape",
                             "per_page": 8},
                     timeout=TIMEOUT)
    r.raise_for_status()
    for video in r.json().get("videos", []):
        fichiers = [f for f in video.get("video_files", [])
                    if f.get("width") and f["width"] >= 1280]
        if not fichiers:
            continue
        lien = sorted(fichiers, key=lambda f: f["width"])[0]["link"]
        identifiant = f"pexels:{video.get('id')}"
        if deja_utilise(identifiant):
            continue
        chemin = os.path.join(
            dossier, hashlib.md5(lien.encode()).hexdigest() + ".mp4")
        _telecharger(lien, chemin)
        _journal_enregistrer(identifiant)
        return chemin
    return None


def pexels_photo(requete, dossier="cache/photos"):
    """Photo Pexels (clé PEXELS_KEY, secours). Renvoie le chemin ou None."""
    cle = os.environ.get("PEXELS_KEY")
    if not cle:
        return None
    os.makedirs(dossier, exist_ok=True)
    r = requests.get("https://api.pexels.com/v1/search",
                     headers={"Authorization": cle},
                     params={"query": requete, "orientation": "landscape",
                             "per_page": 5},
                     timeout=TIMEOUT)
    r.raise_for_status()
    for photo in r.json().get("photos", []):
        lien = photo["src"]["large2x"]
        identifiant = f"pexels:{photo.get('id')}"
        if deja_utilise(identifiant):
            continue
        chemin = os.path.join(dossier, f"{photo['id']}.jpg")
        _telecharger(lien, chemin)
        _journal_enregistrer(identifiant)
        return chemin
    return None


def trouver_visuel(requete, alternative=None):
    """Visuel pour une scène : clip vidéo d'abord, sinon photo animée.
    Pixabay en priorité, Pexels en secours. Renvoie (chemin, type)."""
    for fonction in (pixabay_video, pexels_video):
        chemin = fonction(requete)
        if chemin:
            return chemin, "video"
    for fonction in (pixabay_photo, pexels_photo):
        chemin = fonction(requete)
        if chemin:
            return chemin, "photo"
    if alternative:
        return trouver_visuel(alternative)
    return None, None


# --- Sons -------------------------------------------------------------

def freesound(requete, dossier="cache/sfx"):
    """Son Freesound, filtré licence CC0 uniquement. Renvoie le chemin ou None."""
    cle = os.environ.get("FREESOUND_KEY")
    if not cle:
        return None
    os.makedirs(dossier, exist_ok=True)
    r = requests.get(
        "https://freesound.org/apiv2/search/text/",
        params={"query": requete, "filter": 'license:"Creative Commons 0"',
                "fields": "id,name,previews,duration", "page_size": 5,
                "token": cle},
        timeout=TIMEOUT)
    r.raise_for_status()
    for son in r.json().get("results", []):
        chemin = os.path.join(dossier, f"{son['id']}.mp3")
        _telecharger(son["previews"]["preview-hq-mp3"], chemin)
        return chemin
    return None


def xeno_canto(requete, dossier="cache/sfx"):
    """Chant d'oiseau Xeno-canto, sans clé. Renvoie le chemin ou None."""
    os.makedirs(dossier, exist_ok=True)
    r = requests.get("https://xeno-canto.org/api/2/recordings",
                     params={"query": requete}, timeout=TIMEOUT)
    r.raise_for_status()
    for enr in r.json().get("recordings", []):
        url = enr.get("file")
        if not url:
            continue
        chemin = os.path.join(dossier, f"xc-{enr['id']}.mp3")
        _telecharger(url, chemin)
        return chemin
    return None


def musique_pixabay(humeur, dossier="cache/musique"):
    """Piste de musique Pixabay selon l'humeur (calme/tension/climax).
    Renvoie le chemin ou None. Les pistes sont libres d'usage commercial."""
    cle = os.environ.get("PIXABAY_KEY")
    if not cle:
        return None
    os.makedirs(dossier, exist_ok=True)
    requetes = {"calme": "calm ambient documentary",
                "tension": "suspense tension cinematic",
                "climax": "epic dramatic orchestral"}
    r = requests.get("https://pixabay.com/api/",
                     params={"key": cle, "q": requetes.get(humeur, "ambient"),
                             "per_page": 5, "category": "music"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    for piste in r.json().get("hits", []):
        url = piste.get("audio")
        if not url:
            continue
        chemin = os.path.join(dossier, f"musique-{humeur}.mp3")
        _telecharger(url, chemin)
        return chemin
    return None
