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

from main import (charger_parametres, erreur_cles,
                  charger_bibliotheques, _boucler_audio)
from gardefous import (EchecGardeFou, verifier_environnement,
                       verifier_script, verifier_scenes_min,
                       verifier_voix, verifier_segment,
                       verifier_manifests, verifier_rendu,
                       verifier_miniature, verifier_upload,
                       ecrire_echec)


def _charger_script(chemin):
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def decouper_scenes(scenes, shard_count, shard_index):
    """Scènes du shard : (id-1) % shard_count == shard_index."""
    return [s for s in scenes
            if (s["id"] - 1) % shard_count == shard_index]


def _avec_delai(fonction, delai_s, nom):
    """Exécute fonction avec un délai dur : au-delà, TimeoutError.
    Le thread est daemon : abandonné proprement à la fin du job,
    on ne reste JAMAIS bloqué sur un appel LLM lent."""
    import threading
    resultat, erreur = {}, {}

    def _cible():
        try:
            resultat["v"] = fonction()
        except Exception as e:
            erreur["e"] = e

    th = threading.Thread(target=_cible, daemon=True)
    th.start()
    th.join(delai_s)
    if th.is_alive():
        raise TimeoutError(f"{nom} : délai {delai_s}s dépassé, abandon")
    if "e" in erreur:
        raise erreur["e"]
    return resultat.get("v")


def stage_prepare(sujet_nom=None):
    from checkpoint import Checkpoint
    from facts import fetch_facts
    from script_gen import construire_prompt, generate_script
    from fact_check import (fact_check, appliquer_fact_check,
                            corriger_et_reverifier,
                            retirer_scenes_signalees)
    from topics import prochain_sujet, lire_csv
    from plan_b import construire_script_secours, completer_script
    import time

    verifier_environnement()
    parametres = charger_parametres()
    dossier_sortie = parametres["dossiers"]["sortie"]
    os.makedirs(dossier_sortie, exist_ok=True)
    cp = Checkpoint(dossier_sortie)
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError(erreur_cles(["GEMINI_API_KEY"]))

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
    if len(faits) < 500:
        from gardefous import echec as _echec
        _echec("G003", f"faits trop courts : {len(faits)} caractères")
    print(f"[rapide] Faits : {len(faits)} caractères.")
    cp.sauver_faits(faits)
    cp.etape("faits", {"sujet": sujet["nom"], "source": source,
                       "caracteres": len(faits)})

    with open("config/prompts/script_master.txt", encoding="utf-8") as f:
        modele_prompt = f.read()
    sfx_autorises, ambiances_autorisees = charger_bibliotheques()
    prompt = construire_prompt(modele_prompt, sujet, faits,
                               sfx_autorises, ambiances_autorisees)
    # ── ALPHA ou BÉTA, obligatoirement une vidéo à la fin ──
    # Erreur du chemin lancé (run #3) : script + fact-check ALPHA
    # pouvaient prendre plus de 15 min (jusqu'à 9 appels LLM en
    # fact-check), le job GitHub était annulé avant les segments.
    # Correction : ALPHA borné (8 min script, 4 min fact-check,
    # 1 seule tentative de correction en chemin rapide), puis BÉTA
    # instantané (faits directs, aucun appel LLM, aucune invention).
    debut_prepare = time.monotonic()
    chemin = "alpha"
    try:
        script = _avec_delai(
            lambda: generate_script(prompt, sfx_autorises,
                                    ambiances_autorisees),
            480, "script ALPHA")
        verifier_script(script, duree_cible_min=sujet.get("duree"))
        print(f"[rapide] Script ALPHA : {len(script['scenes'])} scènes, "
              f"titre « {script['meta']['titre']} ».")
    except Exception as e:
        print(f"[rapide] ALPHA script impossible ({e}) : bascule BÉTA.")
        script = construire_script_secours(faits, sujet)
        chemin = "beta"
    cp.sauver_script(script)
    cp.etape("script", {"scenes": len(script["scenes"]),
                        "titre": script["meta"]["titre"],
                        "chemin": chemin})

    if chemin == "alpha":
        try:
            with open("config/prompts/fact_check.txt",
                      encoding="utf-8") as f:
                prompt_check = f.read()

            def _fact_check_alpha():
                resultat = fact_check(script, faits, prompt_check)
                nb = appliquer_fact_check(script, resultat)
                journal = []
                restants = [s["id"] for s in script["scenes"]
                            if s.get("a_verifier")]
                # Chemin rapide : 1 seule tentative (pas 4) ; le
                # BÉTA complète ensuite, au lieu de brûler 9 appels.
                if restants:
                    restants, journal = corriger_et_reverifier(
                        script, faits, prompt_check, resultat,
                        max_tentatives=1)
                return restants, journal

            scenes_a_verifier, journal_corr = _avec_delai(
                _fact_check_alpha, 240, "fact-check ALPHA")
            if scenes_a_verifier:
                print(f"[rapide] Fact-check : scènes signalées "
                      f"{scenes_a_verifier} -> complétées par BÉTA.")
                script = completer_script(script, faits, sujet)
                chemin = "alpha+beta"
                cp.sauver_script(script)
                cp.etape("beta_completion",
                         {"scenes_restantes": len(script["scenes"])})
        except Exception as e:
            # Fact-check impossible : on ne publie PAS une narration
            # non vérifiée. On bascule entièrement sur le BÉTA,
            # dont les phrases sont les faits eux-mêmes.
            print(f"[rapide] Fact-check impossible ({e}) : "
                  f"bascule BÉTA complète.")
            script = construire_script_secours(faits, sujet)
            chemin = "beta"
            cp.sauver_script(script)
            cp.etape("script_beta", {"scenes": len(script["scenes"])})
    verifier_scenes_min(script["scenes"])
    print(f"[rapide] Prepare terminé en "
          f"{time.monotonic() - debut_prepare:.0f}s : "
          f"{len(script['scenes'])} scènes, chemin {chemin}.")
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

    verifier_environnement()
    parametres = charger_parametres()
    dossier_cache = parametres["dossiers"]["cache"]
    script = _charger_script(script_path)
    verifier_script(script)
    scenes = decouper_scenes(script["scenes"], shard_count, shard_index)
    # REPRENDRE : les scènes déjà montées et valides ne sont pas
    # refaites. On garde leur manifest, on ne traite que le reste.
    from reprendre import charger_manifest_shard
    deja = {}
    man_existant = charger_manifest_shard(dossier_sortie, shard_index)
    if man_existant:
        for entree in man_existant.get("scenes", []):
            seg = entree.get("segment")
            voix = entree.get("voice_path")
            if (seg and os.path.exists(seg)
                    and os.path.getsize(seg) > 0
                    and voix and os.path.exists(voix)
                    and os.path.getsize(voix) > 0):
                deja[entree["id"]] = entree
    if deja:
        print(f"[reprendre] Shard {shard_index} : "
              f"{len(deja)} scène(s) déjà faite(s), reprise.")
    scenes_a_faire = [sc for sc in scenes if sc["id"] not in deja]
    scenes = scenes_a_faire
    print(f"[rapide] Shard {shard_index}/{shard_count} : "
          f"{len(scenes)} scène(s) à faire, {len(deja)} reprise(s).")
    scenes = make_voice(scenes, os.path.join(dossier_cache, "voice"))
    verifier_voix(scenes)
    dossier_shard = os.path.join(dossier_sortie, f"shard-{shard_index}")
    os.makedirs(os.path.join(dossier_cache, "segments"), exist_ok=True)
    os.makedirs(dossier_shard, exist_ok=True)
    manifest, photo_miniature = [], None
    if man_existant:
        photo_miniature = man_existant.get("photo_miniature")
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
        verifier_segment(segment, s["duree_reelle_s"], s["id"])
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
        # Sauvegarde progressive : si le job meurt à la scène suivante,
        # les scènes déjà faites ne sont pas perdues.
        with open(os.path.join(dossier_shard, "manifest.json"), "w",
                  encoding="utf-8") as _f:
            json.dump({"shard": shard_index, "scenes": manifest,
                       "photo_miniature": photo_miniature}, _f,
                      ensure_ascii=False, indent=2)
    # Fusion reprise : déjà-faites + nouvelles, triées par id.
    if deja:
        manifest = sorted(list(deja.values()) + manifest,
                          key=lambda e: e["id"])
    # Copier segments + voix dans le dossier du shard (artefact du job).
    import shutil
    for entree in manifest:
        for cle in ("segment", "voice_path"):
            dest = os.path.join(dossier_shard, os.path.basename(entree[cle]))
            if os.path.abspath(entree[cle]) != os.path.abspath(dest):
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
    from render import generer_srt, assembler_rapide, valider_frames
    from thumbnail import make_thumbnail
    from shorts import extraire_shorts_parallele
    from upload import upload, upload_short, calculer_publish_at
    from topics import marquer_publie

    verifier_environnement()
    parametres = charger_parametres()
    dossier_cache = parametres["dossiers"]["cache"]
    os.makedirs(dossier_sortie, exist_ok=True)
    cp = Checkpoint(dossier_sortie)
    manquantes_yt = [c for c in
                     ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
                     if not os.environ.get(c)]
    if manquantes_yt:
        raise RuntimeError(erreur_cles(manquantes_yt))
    script = _charger_script(script_path)
    with open(sujet_path, encoding="utf-8") as f:
        sujet_info = json.load(f)
    sujet, source = sujet_info["sujet"], sujet_info["source"]

    # Correction run #4 : seuls les dossiers avec manifest comptent.
    from reprendre import dossiers_shards_valides
    dossiers_shards, ignores = dossiers_shards_valides(dossier_sortie)
    # dossiers_shards passé en argument est ignoré ici au profit de
    # la vérification réelle sur disque (même dossier par défaut).
    if ignores:
        print(f"[reprendre] Dossiers shard ignorés (sans manifest) : "
              f"{ignores}")
    if not dossiers_shards:
        from gardefous import echec as _echec
        _echec("G008", "aucun dossier shard avec manifest.json "
                       "dans output/ : rien à assembler")
    scenes = []
    photo_miniature, credits_assets = None, []
    for dossier in dossiers_shards:
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
    # Garde-fou AVANT tout mix/rendu cher : manifests + fichiers.
    _manifests = []
    for dossier in dossiers_shards:
        with open(os.path.join(dossier, "manifest.json"),
                  encoding="utf-8") as _f:
            _manifests.append(json.load(_f))
    verifier_manifests(script["scenes"], _manifests)
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
    # Montage EN MÊME TEMPS : les segments sont déjà montés en
    # parallèle par les shards ; le final est un collage sans
    # ré-encodage (secondes, pas minutes). Les sous-titres restent
    # dans le .srt séparé.
    assembler_rapide(segments, audio_final, video_finale)
    verifier_rendu(video_finale, duree_totale)
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
    verifier_miniature(miniature)
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
        verifier_upload(video_id)
        print(f"[rapide] Vidéo uploadée en privé/planifiée : {video_id}.")
        for short in extraire_shorts_parallele(video_finale, script):
            sid = upload_short(short, meta, publish_at)
            print(f"[rapide] Short uploadé : {sid}.")
    cp.sauver_script(script)
    cp.etape("termine", {"video": video_finale, "youtube": video_id})
    marquer_publie(sujet, source)
    print(f"[rapide] Terminé : {video_finale} "
          f"(YouTube : {video_id or 'non publié'}).")
    return video_finale


def stage_reprendre(dossier_sortie, shard_count=10):
    """Reprend EXACTEMENT là où le dernier run s'est arrêté.

    - Script/sujet déjà là : on ne régénère rien.
    - Shards terminés : on ne les relance pas.
    - Shards manquants : on ne refait que ceux-là.
    - Puis assemble + livraison de la vraie vidéo.
    """
    from reprendre import etat_reprise
    etat = etat_reprise(dossier_sortie, shard_count)
    print(f"[reprendre] État : script={etat['script']}, "
          f"sujet={etat['sujet']}, shards terminés="
          f"{etat['shards_termines']}, manquants="
          f"{etat['shards_manquants']}.")
    if not (etat["script"] and etat["sujet"]):
        print("[reprendre] Script manquant : prepare ALPHA/BÉTA.")
        stage_prepare()
    for shard_index in etat["shards_manquants"]:
        print(f"[reprendre] Shard {shard_index} manquant : on le fait.")
        stage_segments(os.path.join(dossier_sortie, "script.json"),
                       shard_index, shard_count, dossier_sortie)
    dossiers = [os.path.join(dossier_sortie, d)
                for d in sorted(os.listdir(dossier_sortie))
                if d.startswith("shard-")]
    return stage_assemble(os.path.join(dossier_sortie, "script.json"),
                          os.path.join(dossier_sortie, "sujet.json"),
                          dossiers, dossier_sortie)


def main():
    parser = argparse.ArgumentParser(
        description="Varus rapide : prepare / segments / assemble.")
    parser.add_argument("--stage", required=True,
                        choices=["prepare", "segments", "assemble",
                                 "reprendre"])
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
        elif args.stage == "reprendre":
            stage_reprendre(args.out, args.shard_count)
        elif args.stage == "segments":
            stage_segments(args.script, args.shard_index,
                           args.shard_count, args.out)
        else:
            dossiers = [os.path.join(args.shards_dir, d)
                        for d in sorted(os.listdir(args.shards_dir))
                        if d.startswith("shard-")]
            stage_assemble(args.script, args.sujet, dossiers, args.out,
                           sans_upload=args.no_upload)
    except EchecGardeFou as e:
        print(f"GARDEFOU {e.code} : {e.message}", file=sys.stderr)
        try:
            ecrire_echec(args.out, e.code, e.message, args.stage)
        except Exception:
            pass
        sys.exit(1)
    except RuntimeError as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # Correction run #4 : une erreur inattendue (fichier manquant,
        # clé absente, etc.) ne doit PLUS mourir sans code lisible
        # en 1 seconde. On l'écrit avec le code G013 + la trace.
        import traceback
        print(f"GARDEFOU G013 : erreur inattendue : {e}",
              file=sys.stderr)
        traceback.print_exc()
        try:
            ecrire_echec(args.out, "G013",
                         f"{type(e).__name__}: {e}", args.stage)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
