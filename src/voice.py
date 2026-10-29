"""Voix off par scène avec Kokoro TTS (open source, Apache 2.0).

Voix française : ff_siwis — la seule voix française du modèle
Kokoro-82M officiel — configurable dans config/settings.yaml
(voix.voix). La vitesse se règle avec voix.vitesse (1.0 = normale).

La durée réelle de la voix (mesurée par ffprobe) fixe la durée de
chaque scène : tout reste synchronisé automatiquement.

Dépendances système : espeak-ng (phonétisation du français, via
misaki). Dépendances Python : kokoro (voir requirements.txt).
Le modèle (~330 Mo) et la voix sont téléchargés depuis Hugging Face
au premier usage, puis mis en cache.
"""

import os
import subprocess
import wave

import numpy as np

VOIX_DEFAUT = "ff_siwis"
TAUX_ECHANTILLONNAGE = 24000
VITESSE_DEFAUT = 1.0
PAUSE_ENTRE_SCENES_S = 0.4

_pipeline = None


def _charger_config_voix():
    """Lit voix.voix / voix.vitesse depuis config/settings.yaml."""
    voix, vitesse = VOIX_DEFAUT, VITESSE_DEFAUT
    chemins = [
        os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "config", "settings.yaml"),
        os.path.join("config", "settings.yaml"),
    ]
    contenu = None
    for chemin in chemins:
        try:
            with open(chemin, encoding="utf-8") as f:
                contenu = f.read()
            break
        except OSError:
            continue
    if contenu is None:
        return voix, vitesse
    try:
        import yaml
        parametres = yaml.safe_load(contenu) or {}
        section = parametres.get("voix", {}) or {}
        voix = section.get("voix", voix) or voix
        vitesse = float(section.get("vitesse", vitesse))
    except Exception:
        # Sans PyYAML : lecture minimale du fichier.
        for ligne in contenu.splitlines():
            ligne = ligne.strip()
            if ligne.startswith('voix: "') and ligne.endswith('"'):
                voix = ligne.split('"')[1] or voix
            elif ligne.startswith("vitesse:"):
                try:
                    vitesse = float(ligne.split(":", 1)[1].strip())
                except ValueError:
                    pass
    return voix, vitesse


def _pipeline_kokoro():
    """Singleton KPipeline français (CPU), modèle chargé au 1er usage."""
    global _pipeline
    if _pipeline is None:
        try:
            from kokoro import KPipeline
        except ImportError as e:
            raise RuntimeError(
                "Kokoro n'est pas installé : ajoutez « kokoro » à "
                "requirements.txt et installez espeak-ng (apt).") from e
        _pipeline = KPipeline(lang_code="f", device="cpu")
    return _pipeline


def _synthese(texte, sortie_mp3, voix, vitesse):
    """Génère un MP3 avec Kokoro (WAV intermédiaire + conversion FFmpeg)."""
    pipeline = _pipeline_kokoro()
    morceaux = []
    for _graph, _phon, audio in pipeline(texte, voice=voix, speed=vitesse):
        if audio is not None:
            morceaux.append(np.asarray(audio, dtype=np.float32))
    if not morceaux:
        raise RuntimeError("Kokoro n'a généré aucun audio.")
    audio = np.concatenate(morceaux)
    wav_tmp = sortie_mp3 + ".wav"
    pcm = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    with wave.open(wav_tmp, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(TAUX_ECHANTILLONNAGE)
        f.writeframes(pcm.tobytes())
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav_tmp,
                    "-codec:a", "libmp3lame", "-q:a", "4", sortie_mp3],
                   check=True)
    os.remove(wav_tmp)


def duree_audio(chemin):
    """Durée réelle d'un fichier audio via ffprobe (secondes)."""
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", chemin])
    return float(out.strip())


def make_voice(scenes, dossier="cache/voice"):
    """Génère un MP3 par scène, ajoute voice_path et duree_reelle_s.

    Le nom de fichier inclut le moteur et la voix : un changement de
    voix ne réutilise jamais un MP3 mis en cache avec une autre voix.
    """
    voix, vitesse = _charger_config_voix()
    os.makedirs(dossier, exist_ok=True)
    for s in scenes:
        chemin = os.path.join(
            dossier, f"scene_{s['id']:03d}_kokoro_{voix}.mp3")
        if not os.path.exists(chemin):
            _synthese(s["narration"], chemin, voix, vitesse)
        s["voice_path"] = chemin
        s["duree_reelle_s"] = round(duree_audio(chemin) + PAUSE_ENTRE_SCENES_S, 2)
    return scenes


def concatener_voix(scenes, sortie):
    """Concatène toutes les voix en une seule piste (fichier liste FFmpeg)."""
    liste = sortie + ".txt"
    with open(liste, "w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"file '{os.path.abspath(s['voice_path'])}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", liste, "-c", "copy", sortie], check=True)
    return sortie
