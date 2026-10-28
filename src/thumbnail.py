"""Miniature 1280x720 (Pillow) : 1 sujet, 3 mots maximum, 1 émotion.

Couleurs par émotion : danger = rouge, mystère = bleu,
émerveillement = jaune. Dégradé sombre en bas pour la lisibilité.
Police : DejaVuSans-Bold (installée dans GitHub Actions) ou, si une
police d'affichage est déposée dans assets/fonts/, la première trouvée.
"""

import os
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

LARGEUR, HAUTEUR = 1280, 720
COULEURS = {"danger": (255, 60, 40),
            "mystere": (120, 200, 255),
            "emerveillement": (255, 210, 60)}


def _police(taille):
    candidats = [
        "assets/fonts/Anton-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for chemin in candidats:
        if os.path.exists(chemin):
            try:
                return ImageFont.truetype(chemin, taille)
            except OSError:
                continue
    return ImageFont.load_default()


def make_thumbnail(photo, texte, sortie, emotion="danger"):
    """photo = image de fond ; texte = 3 mots max (majuscules forcées)."""
    mots = texte.upper().split()
    if len(mots) > 3:
        raise ValueError(
            f"Miniature : texte de {len(mots)} mots, maximum 3 autorisés.")
    img = Image.open(photo).convert("RGB").resize((LARGEUR, HAUTEUR))
    img = ImageEnhance.Contrast(img).enhance(1.25)
    img = ImageEnhance.Color(img).enhance(1.3)

    # Dégradé sombre en bas pour la lisibilité du texte
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for y in range(360, HAUTEUR):
        d.line([(0, y), (LARGEUR, y)], fill=(0, 0, 0, int((y - 360) * 0.7)))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    couleur = COULEURS.get(emotion, COULEURS["danger"])
    police = _police(150)
    d = ImageDraw.Draw(img)
    d.text((60, 520), " ".join(mots), font=police, fill=couleur,
           stroke_width=8, stroke_fill=(0, 0, 0))
    img.save(sortie, quality=92)
    return sortie
