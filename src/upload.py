"""Publication YouTube Data API v3.

Mode relecture par défaut : vidéo privée + planifiée (publishAt),
déclaration de contenu synthétique/IA activée.
Les projets API non audités par Google voient leurs vidéos forcées
en privé : demander l'audit de conformité dès le début.
"""

import os
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CATEGORIE_ANIMAUX = "15"


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
    while resp is None:
        _statut, resp = req.next_chunk()
    video_id = resp["id"]
    if miniature and os.path.exists(miniature):
        yt.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(miniature)).execute()
    return video_id


def upload_short(video, meta, publish_at_iso=None):
    """Upload un Short (même métadonnées + #Shorts dans le titre)."""
    meta_short = dict(meta)
    meta_short["titre"] = (meta["titre"][:55] + " #Shorts"
                           if len(meta["titre"]) > 55
                           else meta["titre"] + " #Shorts")
    return upload(video, None, meta_short, publish_at_iso)
