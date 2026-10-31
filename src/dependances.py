"""Vérification des dépendances Varus, réelle et professionnelle.

Toutes les dépendances du projet, sans exception :
- Système : ffmpeg, ffprobe (montage, voix, validation).
- Python (requirements.txt) : kokoro, requests, pyyaml, pillow,
  google-api-python-client, google-auth-oauthlib, gspread.
- Fichiers de configuration : config/settings.yaml,
  config/prompts/script_master.txt, config/prompts/fact_check.txt,
  config/sfx_library.json, config/ambiances.json.

La vérification utilise find_spec (aucun import lourd) : elle est
instantanée et ne télécharge rien. Si une dépendance manque, on
s'arrête IMMÉDIATEMENT avec le code G012 et la liste exacte, au lieu
de brûler un run entier pour découvrir à la fin que ffmpeg ou un
module manque. Les workflows installent déjà tout (apt + pip) ;
ce module PROUVE que l'installation a réussi, avant tout travail cher.
"""

import importlib.util
import os
import shutil

SYSTEME = ["ffmpeg", "ffprobe"]

# module Python -> nom pip (pour le message d'erreur)
MODULES = {
    "kokoro": "kokoro",
    "requests": "requests",
    "yaml": "pyyaml",
    "PIL": "pillow",
    "googleapiclient": "google-api-python-client",
    "google.oauth2": "google-auth-oauthlib",
    "gspread": "gspread",
}

CONFIGS = [
    "config/settings.yaml",
    "config/prompts/script_master.txt",
    "config/prompts/fact_check.txt",
    "config/sfx_library.json",
    "config/ambiances.json",
]


def verifier_dependances():
    """Renvoie (manquantes_systeme, manquants_python, configs_absents).

    Ne lève rien : l'appelant décide (garde-fou G012).
    """
    manq_sys = [c for c in SYSTEME if shutil.which(c) is None]
    manq_py = []
    for mod, pip in MODULES.items():
        try:
            present = importlib.util.find_spec(mod) is not None
        except Exception:
            present = False
        if not present:
            manq_py.append(pip)
    manq_cfg = [c for c in CONFIGS if not os.path.exists(c)]
    return manq_sys, manq_py, manq_cfg


def exiger_dependances():
    """G012 si une dépendance manque, avec la liste exacte."""
    from gardefous import echec
    manq_sys, manq_py, manq_cfg = verifier_dependances()
    details = []
    if manq_sys:
        details.append("système manquant : " + ", ".join(manq_sys))
    if manq_py:
        details.append("modules Python manquants (pip install) : "
                       + ", ".join(manq_py))
    if manq_cfg:
        details.append("fichiers de configuration absents : "
                       + ", ".join(manq_cfg))
    if details:
        echec("G012", "; ".join(details))
    return True
