"""Sauvegardes progressives (checkpoints) dans le dossier de sortie.

Politique décidée par Sam (« Bloquer mais tout sauvegarder ») : quand
le pipeline s'arrête — blocage fact-check, rendu invalide, ou panne en
cours de route — RIEN ne doit être perdu. Chaque étape écrit donc son
résultat dans output/ DÈS qu'il existe, et l'étape d'archivage de
publish.yml (if: always()) récupère tout, même si le run est tué.

Fichiers produits dans output/ :
- script.json : script courant (écrit dès la génération, rafraîchi à
  chaque correction et à chaque échec).
- checkpoint.json : état d'avancement (étapes franchies + métadonnées).
- faits.txt : sources factuelles utilisées (relecture humaine).
- blocage.json : sur blocage fact-check — script + scènes signalées +
  problèmes + contexte (sujet, source, tentatives de correction).
- echec.json : sur toute autre panne après la génération du script —
  étape atteinte + erreur + script courant.
- validation_frames.json : rapport du contrôle rendu (voir render.py).

Écritures atomiques (fichier temporaire + os.replace) pour ne jamais
laisser un JSON tronqué si le run est tué pendant l'écriture.
"""

import json
import os


class Checkpoint:
    """Journal d'avancement + sauvegardes dans le dossier de sortie."""

    def __init__(self, dossier_sortie):
        self.dossier = dossier_sortie
        os.makedirs(dossier_sortie, exist_ok=True)
        self.etat = {"etapes": []}

    def _ecrire_json(self, nom, donnees):
        chemin = os.path.join(self.dossier, nom)
        tmp = chemin + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(donnees, f, ensure_ascii=False, indent=2)
        os.replace(tmp, chemin)
        return chemin

    def _ecrire_texte(self, nom, texte):
        chemin = os.path.join(self.dossier, nom)
        tmp = chemin + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(texte)
        os.replace(tmp, chemin)
        return chemin

    def etape(self, nom, donnees=None):
        """Marque une étape franchie et persiste l'état d'avancement."""
        if nom not in self.etat["etapes"]:
            self.etat["etapes"].append(nom)
        if donnees is not None:
            self.etat[nom] = donnees
        self._ecrire_json("checkpoint.json", self.etat)
        return self.etat

    def sauver_faits(self, faits):
        return self._ecrire_texte("faits.txt", faits or "")

    def sauver_script(self, script):
        return self._ecrire_json("script.json", script)

    def sauver_blocage(self, script, scenes_signalees, problemes, contexte):
        """Sauvegarde COMPLÈTE sur blocage : script + signalements +
        problèmes + contexte. Ne lève jamais (la sauvegarde ne doit pas
        masquer l'erreur d'origine)."""
        try:
            self.sauver_script(script)
            self._ecrire_json("blocage.json", {
                "raison": "publication bloquée (fact-check)",
                "scenes_signalees": list(scenes_signalees),
                "problemes": problemes or [],
                "contexte": contexte or {},
            })
        except Exception as e:
            print(f"[checkpoint] ATTENTION : sauvegarde blocage impossible "
                  f"({type(e).__name__} : {e})")

    def sauver_echec(self, script, etape, erreur):
        """Sauvegarde sur panne : script courant + étape + erreur."""
        try:
            if script is not None:
                self.sauver_script(script)
            self._ecrire_json("echec.json", {
                "etape": etape,
                "erreur": f"{type(erreur).__name__}: {erreur}",
            })
        except Exception as e:
            print(f"[checkpoint] ATTENTION : sauvegarde échec impossible "
                  f"({type(e).__name__} : {e})")
