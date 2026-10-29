"""Robot Varus — chef d'orchestre du pipeline « Territoire Brut ».

Usage :
    python src/main.py                  # une vidéo complète (clés requises)
    python src/main.py --dry-run        # test sans clés : faits + validation
                                        # du script d'exemple, puis liste
                                        # proprement les clés manquantes
    python src/main.py --topic Guépard  # forcer un sujet (test local)
    python src/main.py --no-upload      # produire sans publier sur YouTube

Les clés API sont lues uniquement depuis les variables d'environnement
(secrets GitHub en production). Aucune clé n'est écrite dans le code.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
sys.path.insert(0, ICI)
os.chdir(RACINE)

CLES_REQUISES = ["GEMINI_API_KEY",
                 "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]
# Visuels et sons : Openverse + Wikimedia Commons sont gratuits et sans clé.
# Pixabay, Pexels, Freesound et Xeno-canto ne sont que des secours
# (utilisés seulement si leurs clés sont présentes).
# SHEET_ID est optionnel : sans lui, on utilise topics.csv.


def charger_parametres():
    """Lit config/settings.yaml (valeurs par défaut si PyYAML absent)."""
    defauts = {"voix": {"voix": "fr-FR-HenriNeural"},
               "publication": {"privacy_status": "private",
                               "delai_publication_heures": 2},
               "dossiers": {"cache": "cache", "sortie": "output"}}
    try:
        import yaml
        with open("config/settings.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return defauts


def cles_manquantes():
    """Liste en français les clés manquantes pour un run complet."""
    return [c for c in CLES_REQUISES if not os.environ.get(c)]


def erreur_cles(manquantes):
    return ("Clés API manquantes pour lancer le pipeline : "
            + ", ".join(manquantes)
            + ". Définissez-les en variables d'environnement "
              "(en production : secrets GitHub, voir README.md). "
              "Aucune vidéo n'a été produite.")


def charger_bibliotheques():
    from script_gen import charger_bibliotheques as charger
    return charger()


def mode_dry_run():
    """Test sans clés : faits + validation du script d'exemple.
    Échoue ensuite proprement en listant les clés manquantes."""
    from facts import fetch_facts
    from script_gen import validate
    from topics import prochain_sujet

    print("[dry-run] Mode test (aucune clé requise pour cette étape).")
    sujet, source = prochain_sujet()
    print(f"[dry-run] Sujet : {sujet['nom']} ({sujet['nom_latin']}) — "
          f"durée {sujet['duree']} min, angle {sujet['angle']} — source {source}.")
    try:
        faits = fetch_facts(sujet)
        print(f"[dry-run] Faits récupérés : {len(faits)} caractères "
              f"(plafond 9 000).")
    except Exception as e:
        print(f"[dry-run] ATTENTION : faits indisponibles "
              f"({type(e).__name__}). "
              "Le test continue avec des faits vides (mode hors ligne).")
        faits = ""
    with open("tests/sample_script.json", encoding="utf-8") as f:
        exemple = json.load(f)
    sfx, ambiances = charger_bibliotheques()
    validate(exemple, sfx, ambiances)
    nb = len(exemple["scenes"])
    print(f"[dry-run] Script d'exemple valide : {nb} scènes, "
          f"ids continus, clip_ia dans la limite, aucune animation "
          "répétée, clés sfx/ambiances autorisées.")
    manquantes = cles_manquantes()
    if manquantes:
        print("[dry-run] " + erreur_cles(manquantes))
        return 1
    print("[dry-run] Toutes les clés sont définies : le pipeline complet "
          "peut être lancé (python src/main.py).")
    return 0


def _boucler_audio(entree, duree_s, sortie):
    """Boucle un fichier audio jusqu'à la durée voulue."""
    subprocess.run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", entree,
                    "-t", str(duree_s), "-c", "copy", sortie], check=True)
    return sortie


def run(sujet_nom=None, sans_upload=False):
    from facts import fetch_facts
    from script_gen import (construire_prompt, generate_script)
    from fact_check import fact_check, appliquer_fact_check
    from topics import prochain_sujet, marquer_publie, lire_csv
    from voice import make_voice, concatener_voix
    from assets import (trouver_visuel, trouver_son, trouver_musique)
    from animate import animer_scene
    from mix import mix_audio
    from render import generer_srt, assembler
    from thumbnail import make_thumbnail
    from shorts import extraire_shorts
    from upload import upload, upload_short, calculer_publish_at

    parametres = charger_parametres()
    dossier_cache = parametres["dossiers"]["cache"]
    dossier_sortie = parametres["dossiers"]["sortie"]
    os.makedirs(dossier_sortie, exist_ok=True)

    manquantes = cles_manquantes()
    if manquantes:
        raise RuntimeError(erreur_cles(manquantes))

    if sujet_nom:
        sujet = next((s for s in lire_csv() if s["nom"] == sujet_nom), None)
        if not sujet:
            raise RuntimeError(f"Sujet « {sujet_nom} » introuvable dans topics.csv.")
        from topics import _normaliser
        sujet, source = _normaliser(sujet), "csv"
    else:
        sujet, source = prochain_sujet()
    print(f"[pipeline] Sujet : {sujet['nom']} (source : {source}).")

    # 1-2. Faits
    faits = fetch_facts(sujet)
    print(f"[pipeline] Faits : {len(faits)} caractères.")

    # 3. Script Gemini
    with open("config/prompts/script_master.txt", encoding="utf-8") as f:
        modele_prompt = f.read()
    sfx_autorises, ambiances_autorisees = charger_bibliotheques()
    prompt = construire_prompt(modele_prompt, sujet, faits,
                               sfx_autorises, ambiances_autorisees)
    script = generate_script(prompt, sfx_autorises, ambiances_autorisees)
    print(f"[pipeline] Script : {len(script['scenes'])} scènes, "
          f"titre « {script['meta']['titre']} ».")

    # 4. Fact-check Gemini
    with open("config/prompts/fact_check.txt", encoding="utf-8") as f:
        prompt_check = f.read()
    resultat = fact_check(script, faits, prompt_check)
    nb_problemes = appliquer_fact_check(script, resultat)
    if resultat["verdict"] != "ok" or nb_problemes:
        print(f"[pipeline] Fact-check : {nb_problemes} problème(s). "
              "Scènes marquées a_verifier.")
    scenes_a_verifier = [s["id"] for s in script["scenes"]
                         if s.get("a_verifier")]
    if scenes_a_verifier:
        with open(os.path.join(dossier_sortie, "script.json"), "w",
                  encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        raise RuntimeError(
            "Publication bloquée : scènes à vérifier : "
            f"{scenes_a_verifier}. Script sauvegardé dans "
            f"{dossier_sortie}/script.json pour relecture humaine.")

    # 5. Voix
    scenes = make_voice(script["scenes"],
                        os.path.join(dossier_cache, "voice"))
    duree_totale = sum(s["duree_reelle_s"] for s in scenes)
    print(f"[pipeline] Voix : durée réelle {duree_totale:.0f} s.")

    # 6-9. Visuels + animation, scène par scène
    segments, photo_miniature, credits_assets = [], None, []
    for s in scenes:
        visuel = s["visuel"]
        chemin, type_visuel = trouver_visuel(
            visuel["requete_pexels_en"], visuel.get("requete_alternative_en"))
        if not chemin:
            raise RuntimeError(
                f"Aucun visuel trouvé pour la scène {s['id']} "
                f"(requête : {visuel['requete_pexels_en']}).")
        if type_visuel == "photo" and photo_miniature is None:
            photo_miniature = chemin
        credits_assets.append({"scene": s["id"], "visuel": chemin,
                               "type": type_visuel})
        segment = os.path.join(dossier_cache, "segments",
                               f"scene_{s['id']:03d}.mp4")
        os.makedirs(os.path.dirname(segment), exist_ok=True)
        animer_scene(chemin, type_visuel, segment,
                     s["duree_reelle_s"], s.get("animation", "ken_burns_zoom_in"))
        segments.append(segment)

    # 10. Mixage : voix + musique + ambiance + sfx
    voix_complete = os.path.join(dossier_cache, "voix_complete.mp3")
    concatener_voix(scenes, voix_complete)
    humeurs = [s.get("musique") for s in scenes if s.get("musique") != "silence"]
    humeur = max(set(humeurs), key=humeurs.count) if humeurs else "calme"
    musique = trouver_musique(humeur)
    musique_bouclee = None
    if musique:
        musique_bouclee = os.path.join(dossier_cache, "musique_bouclee.mp3")
        _boucler_audio(musique, duree_totale, musique_bouclee)
    premiere_ambiance = scenes[0].get("ambiance")
    ambiance = None
    if premiere_ambiance:
        with open("config/ambiances.json", encoding="utf-8") as f:
            requete_amb = json.load(f)[premiere_ambiance]["query"]
        ambiance = trouver_son(requete_amb)
    sfx_pistes, t = [], 0.0
    with open("config/sfx_library.json", encoding="utf-8") as f:
        requetes_sfx = json.load(f)
    for s in scenes:
        for effet in s.get("sfx", []):
            chemin = trouver_son(requetes_sfx[effet["cle"]]["query"])
            if chemin:
                delai = t + {"debut": 0, "milieu": s["duree_reelle_s"] / 2,
                             "fin": max(0, s["duree_reelle_s"] - 1),
                             "continu": 0}.get(effet.get("moment", "debut"), 0)
                sfx_pistes.append((chemin, delai, effet.get("volume", 0.5)))
        t += s["duree_reelle_s"]
    audio_final = os.path.join(dossier_cache, "audio_final.wav")
    mix_audio(voix_complete, musique_bouclee, ambiance, audio_final,
              sfx=sfx_pistes)

    # Rendu + sous-titres
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    base = f"{sujet['nom'].lower().replace(' ', '_')}-{horodatage}"
    video_finale = os.path.join(dossier_sortie, f"{base}.mp4")
    srt = os.path.join(dossier_sortie, f"{base}.srt")
    generer_srt(scenes, srt)
    assembler(segments, audio_final, video_finale, srt)
    print(f"[pipeline] Vidéo rendue : {video_finale}.")

    # 11. Miniature
    meta = script["meta"]
    if photo_miniature is None:
        photo_miniature = os.path.join(dossier_cache, "miniature.jpg")
        subprocess.run(["ffmpeg", "-y", "-i", segments[0], "-vframes", "1",
                        photo_miniature], check=True)
    miniature = os.path.join(dossier_sortie, f"{base}-miniature.jpg")
    make_thumbnail(photo_miniature, meta["miniature"]["texte"], miniature,
                   meta["miniature"].get("emotion", "danger"))

    # Crédits (sources, licences)
    with open(os.path.join(dossier_sortie, f"{base}-credits.json"), "w",
              encoding="utf-8") as f:
        json.dump({"sujet": sujet, "sources": meta.get("sources", []),
                   "assets": credits_assets,
                   "voix": "edge-tts fr-FR-HenriNeural",
                   "contenu_ia": True}, f, ensure_ascii=False, indent=2)

    # 12-13. Shorts + publication (privée / planifiée)
    video_id = None
    if not sans_upload:
        publish_at = calculer_publish_at(
            parametres["publication"].get("delai_publication_heures", 2))
        video_id = upload(video_finale, miniature, meta, publish_at,
                          parametres["publication"].get("privacy_status",
                                                       "private"))
        print(f"[pipeline] Vidéo uploadée en privé/planifiée : {video_id} "
              f"(publication : {publish_at}).")
        for short in extraire_shorts(video_finale, script):
            sid = upload_short(short, meta, publish_at)
            print(f"[pipeline] Short uploadé : {sid}.")
    else:
        print("[pipeline] Option --no-upload : publication ignorée.")

    with open(os.path.join(dossier_sortie, "script.json"), "w",
              encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    marquer_publie(sujet, source)
    print(f"[pipeline] Terminé : {video_finale} "
          f"(YouTube : {video_id or 'non publié'}).")
    return video_finale


def main():
    parser = argparse.ArgumentParser(
        description="Robot Varus — pipeline « Territoire Brut » : documentaire animalier automatisé.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Test sans clés : faits + validation du script d'exemple.")
    parser.add_argument("--topic", help="Forcer un sujet par son nom (test local).")
    parser.add_argument("--no-upload", action="store_true",
                        help="Produire la vidéo sans publier sur YouTube.")
    args = parser.parse_args()
    try:
        if args.dry_run:
            sys.exit(mode_dry_run())
        run(sujet_nom=args.topic, sans_upload=args.no_upload)
    except RuntimeError as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
