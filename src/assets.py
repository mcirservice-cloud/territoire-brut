"""Images, clips et sons — robot Varus.

Priorité (gratuits, sans clé obligatoire) :
- Openverse : photos et sons, licences Creative Commons.
- Wikimedia Commons : photos et vidéos, sans clé, ne bloque jamais.
Secours (seulement si leurs clés sont présentes) :
- Pixabay, Pexels, Freesound, Xeno-canto.

- 1 requête par scène maximum, mais chaque requête est essayée sous
  plusieurs formes : phrase complète, puis simplifiée mot à mot par la
  fin jusqu'au premier mot (ex. « cheetah running fast dry grass » →
  « cheetah running fast » → « cheetah running » → « cheetah »). Une
  requête trop précise ne tue donc plus le run : la forme la plus
  simple (souvent le nom de l'animal) trouve presque toujours un visuel.
- Cache local + journal d'usage (cache/usage_log.json) : un clip déjà
  utilisé dans une vidéo récente n'est pas réutilisé.
- Attributions (titre, auteur, licence) journalisées dans
  cache/attributions.json.
"""

import hashlib
import json
import os
import time

import requests

TIMEOUT = 60
# Timeout court pour les RECHERCHES (API) : une recherche doit répondre
# vite ; un fournisseur lent ne doit pas manger tout le budget de la
# scène à lui seul (run #12 : 300 s brûlés, puis abandon total).
# Le téléchargement garde TIMEOUT (60 s).
SEARCH_TIMEOUT = 5
TAILLE_MAX_TELECHARGEMENT_OCTETS = 2_000_000_000  # 2 Go : un clip de
# scène fait quelques dizaines de Mo ; au-delà, on refuse plutôt que
# de charger tout le fichier en RAM (l'ancien r.content pouvait tuer
# le runner sur un gros fichier).
BUDGET_VISUEL_SCENE_S = 20  # plafond par scène, mode rapide (<20 min) : au-delà, secours immédiat
# (formes simplifiées × fournisseurs) : au-delà, on passe au secours
# (générique / réutilisation / image locale) au lieu de brûler le
# budget du job. Run #13 : 300 s par scène × 56 scènes + rendu ont
# dépassé la limite GitHub de 2 h (46/56 scènes seulement).
JOURNAL = "cache/usage_log.json"
INTERDIT_NB_VIDEOS = 5


UA = "Varus/1.0 (robot documentaire Territoire Brut ; contact via YouTube)"


def _telecharger(url, chemin):
    """Télécharge en streaming (jamais tout en RAM) avec un plafond de
    taille : au-delà, le fichier partiel est supprimé et une erreur
    claire est levée."""
    if os.path.exists(chemin):
        return chemin
    with requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA},
                      stream=True) as r:
        r.raise_for_status()
        total = 0
        with open(chemin, "wb") as f:
            for morceau in r.iter_content(chunk_size=1024 * 256):
                if not morceau:
                    continue
                total += len(morceau)
                if total > TAILLE_MAX_TELECHARGEMENT_OCTETS:
                    f.close()
                    os.remove(chemin)
                    raise RuntimeError(
                        f"Fichier trop volumineux, téléchargement refusé "
                        f"(>{TAILLE_MAX_TELECHARGEMENT_OCTETS} octets) : "
                        f"{url[:120]}")
                f.write(morceau)
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
                     timeout=SEARCH_TIMEOUT)
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
                     timeout=SEARCH_TIMEOUT)
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
                     timeout=SEARCH_TIMEOUT)
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
                     timeout=SEARCH_TIMEOUT)
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
                     headers={"User-Agent": UA}, timeout=SEARCH_TIMEOUT)
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
                     headers={"User-Agent": UA}, timeout=SEARCH_TIMEOUT)
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


def _formes_requete(requete):
    """Formes d'une requête, de la plus précise à la plus simple :
    phrase complète, puis un mot de moins à chaque fois, jusqu'au
    premier mot. Ex. : « cheetah running fast dry grass » →
    « cheetah running fast » → « cheetah running » → « cheetah »."""
    mots = (requete or "").split()
    return [" ".join(mots[:n]) for n in range(len(mots), 0, -1)]


def _formes_courtes(requete):
    """Formes utiles seulement : phrase complète, 3 premiers mots,
    1er mot. Les formes intermédiaires (un mot de moins à chaque fois)
    échouent presque toujours comme la forme complète quand les
    fournisseurs sont en panne, et coûtent chacune plusieurs appels :
    run #13, c'est ce qui a mangé le budget de 2 h."""
    mots = (requete or "").split()
    if not mots:
        return []
    formes = [" ".join(mots), " ".join(mots[:3]), mots[0]]
    vues, uniques = set(), []
    for forme in formes:
        if forme not in vues:
            vues.add(forme)
            uniques.append(forme)
    return uniques


def trouver_visuel(requete, alternative=None, budget_s=BUDGET_VISUEL_SCENE_S):
    """Visuel pour une scène : VRAIE VIDÉO D'ABORD, photo ensuite.
    Changement « montage pro » (Sam, 2026-10-31) : l'ancien ordre
    cherchait la photo dès que la vidéo manquait sur la forme
    complète — résultat, au run #13, toutes les scènes ont fini en
    photo alors qu'une vidéo existait sous une forme simplifiée
    (souvent le seul nom de l'animal). Désormais deux passes :
    passe 1 = vidéo sur toutes les formes (complète, 3 mots, 1 mot,
    puis formes de l'alternative) ; passe 2 = photo seulement si
    AUCUNE vidéo n'a été trouvée. Une scène sans vidéo reste
    possible, mais ce n'est plus jamais le premier choix.
    Budget : la recherche s'arrête après budget_s secondes et
    renvoie (None, None) avec un log clair."""
    requetes = _formes_courtes(requete)
    if alternative:
        requetes += _formes_courtes(alternative)
    vues = set()
    requetes_uniques = []
    for req in requetes:
        if req not in vues:
            vues.add(req)
            requetes_uniques.append(req)
    echeance = time.monotonic() + budget_s

    def _essayer(fonctions, req):
        for fonction in fonctions:
            if time.monotonic() > echeance:
                return None
            try:
                chemin = fonction(req)
            except Exception as exc:
                print(f"[visuel] {fonction.__name__} « {req} » : "
                      f"{type(exc).__name__}")
                continue
            if chemin:
                return chemin
        return None

    # Passe 1 : vraie vidéo, toutes formes.
    for req in requetes_uniques:
        if time.monotonic() > echeance:
            print(f"[visuel] Budget de recherche dépassé ({budget_s}s) "
                  f"pour « {requete} » : abandon de la recherche.")
            break
        if req != requete:
            print(f"[visuel] Vidéo cherchée sous forme simplifiée : "
                  f"« {req} »")
        chemin = _essayer((wikimedia_video, pixabay_video, pexels_video),
                          req)
        if chemin:
            return chemin, "video"
    # Passe 2 : photo seulement, aucune vidéo trouvée.
    for req in requetes_uniques:
        if time.monotonic() > echeance:
            print(f"[visuel] Budget de recherche dépassé ({budget_s}s) "
                  f"pour « {requete} » : abandon de la recherche.")
            break
        chemin = _essayer((openverse_photo, wikimedia_photo,
                           pixabay_photo, pexels_photo), req)
        if chemin:
            return chemin, "photo"
    return None, None


def generer_image_secours(texte, dossier="cache/photos"):
    """Image de secours 1080p, générée localement avec PIL (aucun réseau,
    aucun fournisseur) : dégradé sombre + nom de l'animal au centre.
    Utilisée UNIQUEMENT quand aucun vrai visuel n'a été trouvé, pour
    qu'une scène sans visuel ne tue plus jamais toute la vidéo
    (échec run #12, scène 1). Le Ken Burns l'anime comme une photo.
    Renvoie le chemin de l'image."""
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(dossier, exist_ok=True)
    largeur, hauteur = 1920, 1080
    img = Image.new("RGB", (largeur, hauteur))
    d = ImageDraw.Draw(img)
    for y in range(hauteur):
        r = int(20 + 30 * y / hauteur)
        g = int(35 + 25 * y / hauteur)
        b = int(20 + 15 * y / hauteur)
        d.line([(0, y), (largeur, y)], fill=(r, g, b))
    try:
        police = ImageFont.truetype("DejaVuSans-Bold.ttf", 96)
    except Exception:
        police = ImageFont.load_default()
    mot = (texte or "Territoire Brut").strip().title()
    boite = d.textbbox((0, 0), mot, font=police)
    largeur_texte = boite[2] - boite[0]
    hauteur_texte = boite[3] - boite[1]
    d.text(((largeur - largeur_texte) / 2,
            (hauteur - hauteur_texte) / 2 - 40),
           mot, font=police, fill=(240, 230, 200))
    try:
        police_petite = ImageFont.truetype("DejaVuSans.ttf", 40)
    except Exception:
        police_petite = ImageFont.load_default()
    sous_titre = "Territoire Brut"
    boite2 = d.textbbox((0, 0), sous_titre, font=police_petite)
    d.text(((largeur - (boite2[2] - boite2[0])) / 2,
            (hauteur - hauteur_texte) / 2 + 80),
           sous_titre, font=police_petite, fill=(200, 190, 160))
    chemin = os.path.join(
        dossier,
        "secours-" + hashlib.md5(mot.encode()).hexdigest() + ".jpg")
    img.save(chemin, "JPEG", quality=90)
    return chemin


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
        timeout=SEARCH_TIMEOUT)
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
                     params=params, timeout=SEARCH_TIMEOUT)
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
                     headers={"User-Agent": UA}, timeout=SEARCH_TIMEOUT)
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
                     timeout=SEARCH_TIMEOUT)
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
