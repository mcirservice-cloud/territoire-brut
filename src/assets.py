"""Images, clips et sons — robot Varus.

Priorité (gratuits, sans clé obligatoire) :
- Openverse : photos et sons, licences Creative Commons.
- Wikimedia Commons : photos et vidéos, sans clé, ne bloque jamais.
Secours (seulement si leurs clés sont présentes) :
- Pixabay, Pexels, Freesound, Xeno-canto.

- 1 requête par scène maximum.
- Cache local + journal d'usage (cache/usage_log.json) : un clip déjà
  utilisé dans une vidéo récente n'est pas réutilisé.
- Attributions (titre, auteur, licence) journalisées dans
  cache/attributions.json.
"""

import hashlib
import json
import os

import requests

TIMEOUT = 60
JOURNAL = "cache/usage_log.json"
INTERDIT_NB_VIDEOS = 5


UA = "Varus/1.0 (robot documentaire Territoire Brut ; contact via YouTube)"


def _telecharger(url, chemin):
    if os.path.exists(chemin):
        return chemin
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
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


def _attribution_enregistrer(titre, createur, licence, url_source):
    """Journalise l'attribution d'un média Creative Commons."""
    chemin = "cache/attributions.json"
    try:
        with open(chemin, encoding="utf-8") as f:
            journal = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        journal = []
    journal.append({"titre": titre, "createur": createur,
                    "licence": licence, "source": url_source})
    journal = journal[-500:]
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False)


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


def openverse_photo(requete, dossier="cache/photos"):
    """Photo Openverse (sans clé, licence commerciale). Renvoie le chemin ou None."""
    os.makedirs(dossier, exist_ok=True)
    r = requests.get("https://api.openverse.org/v1/images/",
                     params={"q": requete, "license_type": "commercial",
                             "page_size": 5},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    for photo in r.json().get("results", []):
        url = photo.get("url")
        if not url:
            continue
        identifiant = f"openverse:{photo.get('id')}"
        if deja_utilise(identifiant):
            continue
        ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
        chemin = os.path.join(dossier, f"openverse-{photo['id']}{ext}")
        _telecharger(url, chemin)
        _journal_enregistrer(identifiant)
        _attribution_enregistrer(photo.get("title"), photo.get("creator"),
                                 photo.get("license"),
                                 photo.get("foreign_landing_url"))
        return chemin
    return None


def _wikimedia(requete, type_fichier, dossier):
    """Recherche Wikimedia Commons (sans clé). Renvoie (url, titre) ou None."""
    r = requests.get("https://commons.wikimedia.org/w/api.php",
                     params={"action": "query", "generator": "search",
                             "gsrsearch": f"filetype:{type_fichier} {requete}",
                             "gsrnamespace": 6, "gsrlimit": 5,
                             "prop": "imageinfo", "iiprop": "url|extmetadata",
                             "format": "json"},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("url")
        if not url:
            continue
        identifiant = f"wikimedia:{page.get('title')}"
        if deja_utilise(identifiant):
            continue
        return url, identifiant
    return None, None


def wikimedia_video(requete, dossier="cache/clips"):
    """Clip vidéo Wikimedia Commons (sans clé). Renvoie le chemin ou None."""
    os.makedirs(dossier, exist_ok=True)
    url, identifiant = _wikimedia(requete, "video", dossier)
    if not url:
        return None
    if not url.lower().endswith((".webm", ".mp4", ".ogv")):
        return None
    ext = os.path.splitext(url.split("?")[0])[1] or ".webm"
    chemin = os.path.join(
        dossier, hashlib.md5(url.encode()).hexdigest() + ext)
    _telecharger(url, chemin)
    _journal_enregistrer(identifiant)
    return chemin


def wikimedia_photo(requete, dossier="cache/photos"):
    """Photo Wikimedia Commons (sans clé). Renvoie le chemin ou None."""
    os.makedirs(dossier, exist_ok=True)
    url, identifiant = _wikimedia(requete, "bitmap|drawing", dossier)
    if not url:
        return None
    ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
    chemin = os.path.join(
        dossier, hashlib.md5(url.encode()).hexdigest() + ext)
    _telecharger(url, chemin)
    _journal_enregistrer(identifiant)
    return chemin


def trouver_visuel(requete, alternative=None):
    """Visuel pour une scène : clip vidéo d'abord, sinon photo animée.
    Ordre : vidéo Wikimedia, Pixabay, Pexels (si clés), puis photo
    Openverse, Wikimedia, Pixabay, Pexels. Renvoie (chemin, type).
    Un fournisseur en panne est simplement ignoré (on passe au suivant)."""
    for fonction in (wikimedia_video, pixabay_video, pexels_video):
        try:
            chemin = fonction(requete)
        except Exception:
            continue
        if chemin:
            return chemin, "video"
    for fonction in (openverse_photo, wikimedia_photo,
                     pixabay_photo, pexels_photo):
        try:
            chemin = fonction(requete)
        except Exception:
            continue
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
    """Chant d'oiseau Xeno-canto (clé XENO_CANTO_API_KEY si présente,
    obligatoire depuis octobre 2025 pour télécharger). Renvoie le chemin ou None."""
    os.makedirs(dossier, exist_ok=True)
    params = {"query": requete}
    cle = os.environ.get("XENO_CANTO_API_KEY")
    if cle:
        params["key"] = cle
    r = requests.get("https://xeno-canto.org/api/3/recordings",
                     params=params, timeout=TIMEOUT)
    r.raise_for_status()
    for enr in r.json().get("recordings", []):
        url = enr.get("file")
        if not url:
            continue
        chemin = os.path.join(dossier, f"xc-{enr['id']}.mp3")
        _telecharger(url, chemin)
        return chemin
    return None


def openverse_audio(requete, dossier="cache/sfx"):
    """Son Openverse (sans clé, licence commerciale). Renvoie le chemin ou None."""
    os.makedirs(dossier, exist_ok=True)
    r = requests.get("https://api.openverse.org/v1/audio/",
                     params={"q": requete, "license_type": "commercial",
                             "page_size": 5},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    for son in r.json().get("results", []):
        url = son.get("url")
        if not url:
            continue
        ext = os.path.splitext(url.split("?")[0])[1] or ".mp3"
        chemin = os.path.join(dossier, f"openverse-{son['id']}{ext}")
        _telecharger(url, chemin)
        _attribution_enregistrer(son.get("title"), son.get("creator"),
                                 son.get("license"),
                                 son.get("foreign_landing_url"))
        return chemin
    return None


def trouver_son(requete, dossier="cache/sfx"):
    """Son : Openverse d'abord, Freesound puis Xeno-canto si leurs clés
    existent. Un fournisseur en panne est simplement ignoré."""
    for fonction in (openverse_audio, freesound, xeno_canto):
        try:
            chemin = fonction(requete, dossier)
        except Exception:
            continue
        if chemin:
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


def trouver_musique(humeur, dossier="cache/musique"):
    """Musique : Openverse d'abord, Pixabay si sa clé est présente."""
    requetes = {"calme": "calm ambient documentary",
                "tension": "suspense tension cinematic",
                "climax": "epic dramatic orchestral"}
    try:
        chemin = openverse_audio(requetes.get(humeur, "ambient"), dossier)
    except Exception:
        chemin = None
    if chemin:
        return chemin
    try:
        return musique_pixabay(humeur, dossier)
    except Exception:
        return None
