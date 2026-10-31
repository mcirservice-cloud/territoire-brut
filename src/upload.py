"""Publication YouTube Data API v3.

Mode relecture par défaut : vidéo privée + planifiée (publishAt),
déclaration de contenu synthétique/IA activée.
Les projets API non audités par Google voient leurs vidéos forcées
en privé : demander l'audit de conformité dès le début.
"""

import os
import time
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
try:
    from googleapiclient.errors import HttpError
except Exception:  # modules Google simulés dans les tests
    class HttpError(Exception):
        pass
from googleapiclient.http import MediaFileUpload

CATEGORIE_ANIMAUX = "15"
DELAI_MAX_UPLOAD_S = 1800  # plafond d'un upload : au-delà, on abandonne
# proprement (vidéo locale conservée dans output/) au lieu de pendre
# jusqu'au timeout job — un chunk bloqué ne doit plus manger le run.


def youtube():
    """Client YouTube authentifié par refresh token (secrets GitHub)."""
    creds = Credentials(
        None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token")
    return build("youtube", "v3", credentials=creds)


def calculer_publish_at(delai_heures=2):
    """Date de publication planifiée (ISO 8601 UTC)."""
    moment = datetime.now(timezone.utc) + timedelta(hours=delai_heures)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def upload(video, miniature, meta, publish_at_iso=None,
           privacy_status="private"):
    """Upload la vidéo + sa miniature. Renvoie l'ID YouTube de la vidéo."""
    if publish_at_iso is None:
        publish_at_iso = calculer_publish_at()
    yt = youtube()
    body = {
        "snippet": {"title": meta["titre"],
                    "description": meta["description"],
                    "tags": meta.get("tags", []),
                    "categoryId": CATEGORIE_ANIMAUX,
                    "defaultLanguage": "fr"},
        "status": {"privacyStatus": privacy_status,
                   "publishAt": publish_at_iso,
                   "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True},  # contenu IA déclaré
    }
    req = yt.videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(video, resumable=True))
    resp = None
    debut = time.monotonic()
    while resp is None:
        if time.monotonic() - debut > DELAI_MAX_UPLOAD_S:
            raise RuntimeError(
                f"Upload YouTube trop long (>{DELAI_MAX_UPLOAD_S}s) : "
                "abandon, la vidéo locale reste dans output/ pour un "
                "nouvel essai.")
        _statut, resp = req.next_chunk(num_retries=5)
    video_id = resp["id"]
    if miniature and os.path.exists(miniature):
        # CORRECTIF reprise n° 6 : un compte YouTube non vérifié pour
        # les miniatures personnalisées renvoie 403 sur thumbnails.set.
        # La vidéo est DÉJÀ envoyée à ce stade : un refus de miniature
        # ne doit plus tuer toute la livraison. On signale et on
        # continue avec la miniature automatique de YouTube ; la
        # miniature personnalisée pourra être posée dans Studio une
        # fois le compte vérifié.
        try:
            yt.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(miniature)).execute()
        except HttpError as e:
            if getattr(e, "resp", None) is not None and e.resp.status == 403:
                print(f"[upload] Miniature refusée (403, compte non "
                      f"vérifié pour les miniatures) : vidéo {video_id} "
                      f"conservée sans miniature personnalisée.")
            else:
                raise
    return video_id


def upload_short(video, meta, publish_at_iso=None):
    """Upload un Short (même métadonnées + #Shorts dans le titre)."""
    meta_short = dict(meta)
    meta_short["titre"] = (meta["titre"][:55] + " #Shorts"
                           if len(meta["titre"]) > 55
                           else meta["titre"] + " #Shorts")
    return upload(video, None, meta_short, publish_at_iso)
