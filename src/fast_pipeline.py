"""Pipeline rapide « Territoire Brut » (< 20 min) : 3 étapes séparées,
pour tourner en jobs GitHub Actions parallèles.

- prepare : sujets + faits + script + fact-check (1 job).
  Sortie : output/script.json (+ faits.txt, checkpoint.json).
- segments --shard-index I --shard-count N : voix + visuels + animation
  pour les scènes où (id-1) % N == I seulement (N jobs en parallèle).
  Sortie : output/shard-I/manifest.json + segments + voix du shard.
- assemble : mix + rendu + miniature + publication (1 job).
  Entrées : output/script.json + output/shard-*/manifest.json.

Le chemin classique (main.py, 1 job) reste intact et inchangé : ce
fichier est une voie parallèle, pas un remplacement. Mêmes règles que
main.py : fact-check avec retrait auto (blocage + sauvegarde si moins
de 20 scènes resteraient), vidéo privée + planifiée, déclaration
contenu synthétique, jamais de publication avec avertissement.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (charger_parametres, cles_manquantes, erreur_cles,
                  charger_bibliotheques, _boucler_audio)


def _charger_script(chemin):
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def decouper_scenes(scenes, shard_count, shard_index):
    """Scènes du shard : (id-1) % shard_count == shard_index."""
    return [s for s in scenes
            if (s["id"] - 1) % shard_count == shard_index]


def stage_prepare(sujet_nom=None):
    from checkpoint import Checkpoint
    from facts import fetch_facts
    from script_gen import construire_prompt, generate_script
    from fact_check import (fact_check, appliquer_fact_check,
                            corriger_et_reverifier,
                            retirer_scenes_signalees)
    from topics import prochain_sujet, lire_csv

    parametres = charger_parametres()
    dossier_sortie = parametres["dossiers"]["sortie"]
    os.makedirs(dossier_sortie, exist_ok=True)
    cp = Checkpoint(dossier_sortie)
    manquantes = cles_manquantes()
    if manquantes:
        raise RuntimeError(erreur_cles(manquantes))

    if sujet_nom:
        sujet = next((s for s in lire_csv() if s["nom"] == sujet_nom), None)
        if not sujet:
            raise RuntimeError(f"Sujet « {sujet_nom} » introuvable.")
        from topics import _normaliser
        sujet, source = _normaliser(sujet), "csv"
    else:
        sujet, source = prochain_sujet()
    print(f"[rapide] Sujet : {sujet['nom']} (source : {source}).")

    faits = fetch_facts(sujet)
    print(f"[rapide] Faits : {len(faits)} caractères.")
    cp.sauver_faits(faits)
    cp.etape("faits", {"sujet": sujet["nom"], "source": source,
                       "caracteres": len(faits)})

    with open("config/prompts/script_master.txt", encoding="utf-8") as f:
        modele_prompt = f.read()
    sfx_autorises, ambiances_autorisees = charger_bibliotheques()
    prompt = construire_prompt(modele_prompt, sujet, faits,
                               sfx_autorises, ambiances_autorisees)
    script = generate_script(prompt, sfx_autorises, ambiances_autorisees)
    print(f"[rapide] Script : {len(script['scenes'])} scènes, "
          f"titre « {script['meta']['titre']} ».")
    cp.sauver_script(script)
    cp.etape("script", {"scenes": len(script["scenes"]),
                        "titre": script["meta"]["titre"]})

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
        print(f"[rapide] Fact-check : {nb_problemes} problème(s). "
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
    if scenes_a_verifier:
        retires = retirer_scenes_signalees(script, scenes_a_verifier)
        if retires:
            cp.sauver_script(script)
            cp.etape("scenes_retirees_fact_check",
                     {"retirees": retires,
                      "scenes_restantes": len(script["scenes"]),
                      "tentatives_correction": journal_corr})
            print(f"[rapide] Scènes retirées {retires}, "
                  f"{len(script['scenes'])} scènes restantes.")
        else:
            cp.sauver_blocage(
                script, scenes_a_verifier,
                resultat.get("problemes", []),
                {"sujet": sujet["nom"], "source": source,
                 "tentatives_correction": journal_corr,
                 "faits_caracteres": len(faits)})
            raise RuntimeError(
                "Publication bloquée : scènes à vérifier : "
                f"{scenes_a_verifier}. TOUT est sauvegardé.")
    # Le sujet est conservé avec le script pour l'étape assemble.
    with open(os.path.join(dossier_sortie, "sujet.json"), "w",
              encoding="utf-8") as f:
        json.dump({"sujet": sujet, "source": source}, f,
                  ensure_ascii=False, indent=2)
    print(f"[rapide] Prepare terminé : {len(script['scenes'])} scènes.")
    return script


def stage_segments(script_path, shard_index, shard_count, dossier_sortie):
    from voice import make_voice
    from assets import trouver_visuel, generer_image_secours
    from animate import animer_scene

    parametres = charger_parametres()
    dossier_cache = parametres["dossiers"]["cache"]
    script = _charger_script(script_path)
    scenes = decouper_scenes(script["scenes"], shard_count, shard_index)
    print(f"[rapide] Shard {shard_index}/{shard_count} : "
          f"{len(scenes)} scènes.")
    scenes = make_voice(scenes, os.path.join(dossier_cache, "voice"))
    dossier_shard = os.path.join(dossier_sortie, f"shard-{shard_index}")
    os.makedirs(os.path.join(dossier_cache, "segments"), exist_ok=True)
    os.makedirs(dossier_shard, exist_ok=True)
    manifest, photo_miniature = [], None
    dernier_visuel, dernier_type = None, None
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
            chemin = generer_image_secours(premier_mot or "Territoire Brut")
            type_visuel, mode_visuel = "photo", "secours"
        print(f"[rapide] Visuel scène {s['id']} : {mode_visuel} "
              f"({type_visuel}).")
        if mode_visuel in ("normal", "generique", "secours"):
            dernier_visuel, dernier_type = chemin, type_visuel
        if type_visuel == "photo" and photo_miniature is None:
            photo_miniature = chemin
        segment = os.path.join(dossier_cache, "segments",
                               f"scene_{s['id']:03d}.mp4")
        animer_scene(chemin, type_visuel, segment,
                     s["duree_reelle_s"],
                     s.get("animation", "ken_burns_zoom_in"))
        manifest.append({
            "id": s["id"], "segment": segment,
            "voice_path": s["voice_path"],
            "duree_reelle_s": s["duree_reelle_s"],
            "narration": s["narration"],
            "animation": s.get("animation", "ken_burns_zoom_in"),
            "musique": s.get("musique"), "ambiance": s.get("ambiance"),
            "sfx": s.get("sfx", []), "visuel": chemin,
            "type_visuel": type_visuel, "mode_visuel": mode_visuel,
        })
    # Copier segments + voix dans le dossier du shard (artefact du job).
    import shutil
    for entree in manifest:
        for cle in ("segment", "voice_path"):
            dest = os.path.join(dossier_shard, os.path.basename(entree[cle]))
            shutil.copyfile(entree[cle], dest)
            entree[cle] = dest
    if photo_miniature:
        dest = os.path.join(dossier_shard, "photo_miniature.jpg")
        try:
            shutil.copyfile(photo_miniature, dest)
            photo_miniature = dest
        except Exception:
            photo_miniature = None
    with open(os.path.join(dossier_shard, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump({"shard": shard_index, "scenes": manifest,
                   "photo_miniature": photo_miniature}, f,
                  ensure_ascii=False, indent=2)
    print(f"[rapide] Shard {shard_index} terminé : "
          f"{len(manifest)} segments.")
    return manifest


def stage_assemble(script_path, sujet_path, dossiers_shards,
                   dossier_sortie, sans_upload=False):
    from checkpoint import Checkpoint
    from exec_utils import run as run_cmd
    from voice import concatener_voix
    from assets import trouver_son, trouver_musique
    from mix import mix_audio
    from render import generer_srt, assembler, valider_frames
    from thumbnail import make_thumbnail
    from shorts import extraire_shorts
    from upload import upload, upload_short, calculer_publish_at
    from topics import marquer_publie

    parametres = charger_parametres()
    dossier_cache = parametres["dossiers"]["cache"]
    os.makedirs(dossier_sortie, exist_ok=True)
    cp = Checkpoint(dossier_sortie)
    manquantes = cles_manquantes()
    if manquantes:
        raise RuntimeError(erreur_cles(manquantes))
    script = _charger_script(script_path)
    with open(sujet_path, encoding="utf-8") as f:
        sujet_info = json.load(f)
    sujet, source = sujet_info["sujet"], sujet_info["source"]

    scenes = []
    photo_miniature, credits_assets = None, []
    for dossier in sorted(dossiers_shards):
        with open(os.path.join(dossier, "manifest.json"),
                  encoding="utf-8") as f:
            man = json.load(f)
        if photo_miniature is None:
            photo_miniature = man.get("photo_miniature")
        for entree in man["scenes"]:
            scenes.append(entree)
            credits_assets.append({"scene": entree["id"],
                                   "visuel": entree["visuel"],
                                   "type": entree["type_visuel"]})
    scenes.sort(key=lambda s: s["id"])
    if len(scenes) != len(script["scenes"]):
        raise RuntimeError(
            f"Assemblage incomplet : {len(scenes)} scènes sur "
            f"{len(script['scenes'])} attendues.")
    segments = [s["segment"] for s in scenes]
    duree_totale = sum(s["duree_reelle_s"] for s in scenes)
    print(f"[rapide] Assemblage : {len(scenes)} scènes, "
          f"{duree_totale:.0f} s.")

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

    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    base = f"{sujet['nom'].lower().replace(' ', '_')}-{horodatage}"
    video_finale = os.path.join(dossier_sortie, f"{base}.mp4")
    srt = os.path.join(dossier_sortie, f"{base}.srt")
    generer_srt(scenes, srt)
    assembler(segments, audio_final, video_finale, srt)
    print(f"[rapide] Vidéo rendue : {video_finale}.")
    params_frames = parametres.get("validation_frames") or {}
    rapport_frames = valider_frames(
        video_finale,
        echantillons=params_frames.get("echantillons", 8),
        seuil_noir=params_frames.get("seuil_noir", 8),
        proportion_max_noires=params_frames.get("proportion_max_noires",
                                                0.8))
    cp.etape("validation_frames", rapport_frames)
    if not rapport_frames["ok"]:
        raise RuntimeError(
            f"Rendu invalide ({rapport_frames.get('raison')}) : "
            "publication bloquée, vidéo conservée pour relecture.")

    meta = script["meta"]
    if photo_miniature is None:
        photo_miniature = os.path.join(dossier_cache, "miniature.jpg")
        run_cmd(["ffmpeg", "-y", "-i", segments[0], "-vframes", "1",
                 photo_miniature], timeout=120)
    miniature = os.path.join(dossier_sortie, f"{base}-miniature.jpg")
    make_thumbnail(photo_miniature, meta["miniature"]["texte"], miniature,
                   meta["miniature"].get("emotion", "danger"))
    with open(os.path.join(dossier_sortie, f"{base}-credits.json"), "w",
              encoding="utf-8") as f:
        json.dump({"sujet": sujet, "sources": meta.get("sources", []),
                   "assets": credits_assets,
                   "voix": (f"{(parametres.get('voix') or {}).get('moteur', 'kokoro')} "
                            f"{(parametres.get('voix') or {}).get('voix', 'ff_siwis')}"),
                   "contenu_ia": True}, f, ensure_ascii=False, indent=2)

    video_id = None
    if not sans_upload:
        publish_at = calculer_publish_at(
            parametres["publication"].get("delai_publication_heures", 2))
        video_id = upload(video_finale, miniature, meta, publish_at,
                          parametres["publication"].get("privacy_status",
                                                       "private"))
        print(f"[rapide] Vidéo uploadée en privé/planifiée : {video_id}.")
        for short in extraire_shorts(video_finale, script):
            sid = upload_short(short, meta, publish_at)
            print(f"[rapide] Short uploadé : {sid}.")
    cp.sauver_script(script)
    cp.etape("termine", {"video": video_finale, "youtube": video_id})
    marquer_publie(sujet, source)
    print(f"[rapide] Terminé : {video_finale} "
          f"(YouTube : {video_id or 'non publié'}).")
    return video_finale


def main():
    parser = argparse.ArgumentParser(
        description="Varus rapide : prepare / segments / assemble.")
    parser.add_argument("--stage", required=True,
                        choices=["prepare", "segments", "assemble"])
    parser.add_argument("--topic", help="Forcer un sujet (prepare).")
    parser.add_argument("--script", default="output/script.json")
    parser.add_argument("--sujet", default="output/sujet.json")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=10)
    parser.add_argument("--shards-dir", default="output")
    parser.add_argument("--out", default="output")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()
    try:
        if args.stage == "prepare":
            stage_prepare(sujet_nom=args.topic)
        elif args.stage == "segments":
            stage_segments(args.script, args.shard_index,
                           args.shard_count, args.out)
        else:
            dossiers = [os.path.join(args.shards_dir, d)
                        for d in sorted(os.listdir(args.shards_dir))
                        if d.startswith("shard-")]
            stage_assemble(args.script, args.sujet, dossiers, args.out,
                           sans_upload=args.no_upload)
    except RuntimeError as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
