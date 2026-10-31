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

Politique « Bloquer mais tout sauvegarder » (décidée par Sam) : chaque
étape écrit son résultat dans output/ dès qu'il existe (voir
src/checkpoint.py) ; en cas de blocage ou de panne, le script, les
signalements et le contexte sont sauvegardés avant l'arrêt, et
l'étape d'archivage (publish.yml, if: always()) les récupère.
"""

import argparse
import json
import os
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
# DEEPSEEK_API_KEY / OPENROUTER_API_KEY sont optionnels : sans elles, la
# réflexion (fact-check) utilise Gemini, jamais bloquant.


def charger_parametres():
    """Lit config/settings.yaml (valeurs par défaut si PyYAML absent)."""
    defauts = {"voix": {"moteur": "kokoro", "voix": "ff_siwis"},
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
    """Boucle un fichier audio jusqu'à la durée voulue (borné)."""
    import os as _os
    if _os.path.dirname(sortie):
        _os.makedirs(_os.path.dirname(sortie), exist_ok=True)
    from exec_utils import run as run_cmd
    run_cmd(["ffmpeg", "-y", "-stream_loop", "-1", "-i", entree,
             "-t", str(duree_s), "-c", "copy", sortie], timeout=300)
    return sortie


def run(sujet_nom=None, sans_upload=False):
    from checkpoint import Checkpoint
    from exec_utils import run as run_cmd
    from facts import fetch_facts
    from script_gen import (construire_prompt, generate_script)
    from fact_check import (fact_check, appliquer_fact_check,
                            corriger_et_reverifier,
                            retirer_scenes_signalees)
    from topics import prochain_sujet, marquer_publie, lire_csv
    from voice import make_voice, concatener_voix
    from assets import (trouver_visuel, trouver_son, trouver_musique,
                        generer_image_secours)
    from animate import animer_scene
    from mix import mix_audio
    from render import generer_srt, assembler, valider_frames
    from thumbnail import make_thumbnail
    from gardefous import (verifier_script, verifier_voix,
                           verifier_segment, verifier_rendu,
                           verifier_miniature, verifier_upload)
    from shorts import extraire_shorts
    from upload import upload, upload_short, calculer_publish_at

    parametres = charger_parametres()
    dossier_cache = parametres["dossiers"]["cache"]
    dossier_sortie = parametres["dossiers"]["sortie"]
    os.makedirs(dossier_sortie, exist_ok=True)
    cp = Checkpoint(dossier_sortie)

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

    # Tout ce qui suit la génération du script est protégé : en cas de
    # blocage ou de panne, l'état courant est sauvegardé avant de
    # propager l'erreur (politique « ne perd jamais rien »).
    script = None
    etape_courante = "sujet"
    try:
        # 1-2. Faits
        etape_courante = "faits"
        faits = fetch_facts(sujet)
        print(f"[pipeline] Faits : {len(faits)} caractères.")
        cp.sauver_faits(faits)
        cp.etape("faits", {"sujet": sujet["nom"], "source": source,
                           "caracteres": len(faits)})

        # 3. Script Gemini
        etape_courante = "script"
        with open("config/prompts/script_master.txt", encoding="utf-8") as f:
            modele_prompt = f.read()
        sfx_autorises, ambiances_autorisees = charger_bibliotheques()
        prompt = construire_prompt(modele_prompt, sujet, faits,
                                   sfx_autorises, ambiances_autorisees)
        script = generate_script(prompt, sfx_autorises, ambiances_autorisees)
        verifier_script(script, duree_cible_min=sujet.get("duree"))
        print(f"[pipeline] Script : {len(script['scenes'])} scènes, "
              f"titre « {script['meta']['titre']} ».")
        cp.sauver_script(script)
        cp.etape("script", {"scenes": len(script["scenes"]),
                            "titre": script["meta"]["titre"]})

        # 4. Fact-check par réflexion (DeepSeek d'abord si la clé existe,
        # sinon Gemini / OpenRouter gratuit) + boucle de correction
        # BORNÉE (max 2 tentatives, config fact_check).
        etape_courante = "fact_check"
        with open("config/prompts/fact_check.txt", encoding="utf-8") as f:
            prompt_check = f.read()
        resultat = fact_check(script, faits, prompt_check)
        nb_problemes = appliquer_fact_check(script, resultat)
        cp.etape("fact_check_initial",
                 {"verdict": resultat.get("verdict"),
                  "problemes": nb_problemes})
        scenes_a_verifier = [s["id"] for s in script["scenes"]
                             if s.get("a_verifier")]
        journal_corr = []
        if scenes_a_verifier:
            print(f"[pipeline] Fact-check : {nb_problemes} problème(s). "
                  "Correction automatique...")
            max_tentatives = (parametres.get("fact_check") or {}).get(
                "correction_max_tentatives", 4)
            scenes_a_verifier, journal_corr = corriger_et_reverifier(
                script, faits, prompt_check, resultat,
                max_tentatives=max_tentatives)
            cp.sauver_script(script)
            cp.etape("correction",
                     {"tentatives": journal_corr,
                      "scenes_restantes": scenes_a_verifier})
            print(f"[pipeline] Correction : {len(journal_corr)} "
                  "tentative(s), "
                  f"{len(scenes_a_verifier)} scène(s) encore signalée(s).")
        if scenes_a_verifier:
            # Auto-ajustement (décision Sam 2026-10-29, après run #11) :
            # au lieu de bloquer pour 1 scène, on retire les scènes
            # encore signalées — l'affirmation signalée n'est donc PAS
            # publiée, ni avec avertissement, ni réécrite à l'aveugle —
            # puis on poursuit le traitement avec le reste du script.
            # Si le retrait laisserait moins de 20 scènes, on retombe
            # sur « Bloquer mais tout sauvegarder ».
            retires = retirer_scenes_signalees(script, scenes_a_verifier)
            if retires:
                cp.sauver_script(script)
                cp.etape("scenes_retirees_fact_check",
                         {"retirees": retires,
                          "scenes_restantes": len(script["scenes"]),
                          "tentatives_correction": journal_corr})
                print(f"[pipeline] Auto-ajustement fact-check : scènes "
                      f"retirées {retires}, "
                      f"{len(script['scenes'])} scènes restantes. "
                      f"Traitement poursuivi.")
                scenes_a_verifier = []
            else:
                cp.sauver_blocage(
                    script, scenes_a_verifier,
                    resultat.get("problemes", []),
                    {"sujet": sujet["nom"], "source": source,
                     "tentatives_correction": journal_corr,
                     "faits_caracteres": len(faits)})
                raise RuntimeError(
                    "Publication bloquée : scènes à vérifier : "
                    f"{scenes_a_verifier}. TOUT est sauvegardé dans "
                    f"{dossier_sortie}/ (script.json + blocage.json) pour "
                    "relecture humaine.")

        # 5. Voix
        etape_courante = "voix"
        scenes = make_voice(script["scenes"],
                            os.path.join(dossier_cache, "voice"))
        verifier_voix(scenes)
        duree_totale = sum(s["duree_reelle_s"] for s in scenes)
        print(f"[pipeline] Voix : durée réelle {duree_totale:.0f} s.")
        cp.etape("voix", {"duree_totale_s": round(duree_totale, 2)})

        # 6-9. Visuels + animation, scène par scène
        # Règle run #12 : une scène sans visuel ne tue PLUS jamais la
        # vidéo. Ordre de secours : requête générique courte, puis
        # réutilisation du visuel précédent, puis image de secours
        # locale (PIL, sans réseau). La scène est signalée dans le
        # checkpoint pour la relecture humaine, mais le traitement
        # continue jusqu'à la vidéo finale.
        etape_courante = "segments"
        segments, photo_miniature, credits_assets = [], None, []
        scenes_visuel_secours, dernier_visuel, dernier_type = [], None, None
        for s in scenes:
            visuel = s["visuel"]
            requete = visuel["requete_pexels_en"]
            chemin, type_visuel = trouver_visuel(
                requete, visuel.get("requete_alternative_en"))
            mode_visuel = "normal"
            if not chemin:
                premier_mot = (requete or "").split()[0] if requete else ""
                for generique in (premier_mot, "savanna"):
                    if not generique:
                        continue
                    chemin, type_visuel = trouver_visuel(
                        generique, budget_s=10)
                    if chemin:
                        mode_visuel = "generique"
                        break
            if not chemin and dernier_visuel:
                chemin, type_visuel, mode_visuel = (
                    dernier_visuel, dernier_type, "reuse")
            if not chemin:
                premier_mot = (requete or "").split()[0] if requete else ""
                chemin = generer_image_secours(
                    premier_mot or "Territoire Brut")
                type_visuel, mode_visuel = "photo", "secours"
                scenes_visuel_secours.append(s["id"])
            print(f"[pipeline] Visuel scène {s['id']} : {mode_visuel} "
                  f"({type_visuel}).")
            if mode_visuel in ("normal", "generique", "secours"):
                dernier_visuel, dernier_type = chemin, type_visuel
            if type_visuel == "photo" and photo_miniature is None:
                photo_miniature = chemin
            credits_assets.append({"scene": s["id"], "visuel": chemin,
                                   "type": type_visuel})
            segment = os.path.join(dossier_cache, "segments",
                                   f"scene_{s['id']:03d}.mp4")
            os.makedirs(os.path.dirname(segment), exist_ok=True)
            animer_scene(chemin, type_visuel, segment,
                         s["duree_reelle_s"], s.get("animation", "ken_burns_zoom_in"))
            verifier_segment(segment, s["duree_reelle_s"], s["id"])
            segments.append(segment)
        cp.etape("segments", {"nb_segments": len(segments),
                              "scenes_visuel_secours": scenes_visuel_secours})

        # 10. Mixage : voix + musique + ambiance + sfx
        etape_courante = "mix"
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
        cp.etape("mix", {"sfx_pistes": len(sfx_pistes)})

        # Rendu + sous-titres + validation du rendu
        etape_courante = "rendu"
        horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        base = f"{sujet['nom'].lower().replace(' ', '_')}-{horodatage}"
        video_finale = os.path.join(dossier_sortie, f"{base}.mp4")
        srt = os.path.join(dossier_sortie, f"{base}.srt")
        generer_srt(scenes, srt)
        assembler(segments, audio_final, video_finale, srt)
        verifier_rendu(video_finale, duree_totale)
        print(f"[pipeline] Vidéo rendue : {video_finale}.")
        params_frames = parametres.get("validation_frames") or {}
        rapport_frames = valider_frames(
            video_finale,
            echantillons=params_frames.get("echantillons", 8),
            seuil_noir=params_frames.get("seuil_noir", 8),
            proportion_max_noires=params_frames.get("proportion_max_noires",
                                                    0.8))
        cp.etape("validation_frames", rapport_frames)
        print(f"[pipeline] Validation frames : ok={rapport_frames['ok']} "
              f"({rapport_frames.get('frames_decodables', '?')} frames "
              f"décodables, {rapport_frames.get('frames_noires', '?')} "
              f"noires).")
        if not rapport_frames["ok"]:
            raise RuntimeError(
                f"Rendu invalide ({rapport_frames.get('raison')}) : "
                "publication bloquée, vidéo conservée dans "
                f"{dossier_sortie}/ pour relecture humaine.")

        # 11. Miniature
        etape_courante = "miniature"
        meta = script["meta"]
        if photo_miniature is None:
            photo_miniature = os.path.join(dossier_cache, "miniature.jpg")
            run_cmd(["ffmpeg", "-y", "-i", segments[0], "-vframes", "1",
                     photo_miniature], timeout=120)
        miniature = os.path.join(dossier_sortie, f"{base}-miniature.jpg")
        make_thumbnail(photo_miniature, meta["miniature"]["texte"], miniature,
                       meta["miniature"].get("emotion", "danger"))
        verifier_miniature(miniature)
        cp.etape("miniature", {"fichier": miniature})

        # Crédits (sources, licences)
        with open(os.path.join(dossier_sortie, f"{base}-credits.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"sujet": sujet, "sources": meta.get("sources", []),
                       "assets": credits_assets,
                       "voix": (f"{(parametres.get('voix') or {}).get('moteur', 'kokoro')} "
                                f"{(parametres.get('voix') or {}).get('voix', 'ff_siwis')}"),
                       "contenu_ia": True}, f, ensure_ascii=False, indent=2)

        # 12-13. Shorts + publication (privée / planifiée)
        etape_courante = "publication"
        video_id = None
        if not sans_upload:
            publish_at = calculer_publish_at(
                parametres["publication"].get("delai_publication_heures", 2))
            video_id = upload(video_finale, miniature, meta, publish_at,
                              parametres["publication"].get("privacy_status",
                                                           "private"))
            verifier_upload(video_id)
            print(f"[pipeline] Vidéo uploadée en privé/planifiée : {video_id} "
                  f"(publication : {publish_at}).")
            for short in extraire_shorts(video_finale, script):
                sid = upload_short(short, meta, publish_at)
                print(f"[pipeline] Short uploadé : {sid}.")
        else:
            print("[pipeline] Option --no-upload : publication ignorée.")

        cp.sauver_script(script)
        cp.etape("termine", {"video": video_finale, "youtube": video_id})
        marquer_publie(sujet, source)
        print(f"[pipeline] Terminé : {video_finale} "
              f"(YouTube : {video_id or 'non publié'}).")
        return video_finale
    except Exception as e:
        # Sauvegarde de secours (ne masque jamais l'erreur d'origine) :
        # script courant + étape + erreur, même si le run est tué juste
        # après — l'archivage if: always() récupère output/.
        cp.sauver_echec(script, etape_courante, e)
        raise


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
