"""Lecture des sujets : CSV local (topics.csv) ou Google Sheet via gspread.

Source de vérité en production : le Google Sheet (SHEET_ID défini).
En local / sans Sheet, on utilise topics.csv (copie de la banque de 60 sujets).
Prend toujours le prochain sujet avec le statut « à produire ».
"""

import csv
import os
from datetime import date

STATUT_A_PRODUIRE = "à produire"
STATUT_PUBLIE = "publié"

COLONNES = ["nom", "nom_latin", "page_wikipedia", "duree", "angle",
            "tension", "statut", "date_publi", "CTR", "retention"]


def _csv_path():
    return os.environ.get("TOPICS_CSV", "topics.csv")


def lire_csv(path=None):
    """Lit topics.csv et renvoie la liste des sujets (dicts)."""
    path = path or _csv_path()
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def lire_sheet():
    """Lit le Google Sheet via gspread. Deux authentifications possibles :
    - jeton OAuth (SHEETS_REFRESH_TOKEN + YT_CLIENT_ID/YT_CLIENT_SECRET),
      obtenu comme pour YouTube via OAuth Playground ;
    - sinon compte de service (fichier JSON indiqué par
      GOOGLE_SERVICE_ACCOUNT_JSON, ou service_account.json à la racine)."""
    import gspread

    sheet_id = os.environ["SHEET_ID"]
    refresh = os.environ.get("SHEETS_REFRESH_TOKEN")
    if refresh:
        from google.oauth2.credentials import Credentials
        creds = Credentials(
            None,
            refresh_token=refresh,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["YT_CLIENT_ID"],
            client_secret=os.environ["YT_CLIENT_SECRET"],
        )
        gc = gspread.authorize(creds)
    else:
        creds_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON",
                                    "service_account.json")
        gc = gspread.service_account(filename=creds_path)
    feuille = gc.open_by_key(sheet_id).sheet1
    return feuille.get_all_records(), feuille


def prochain_sujet():
    """Renvoie (sujet, source) avec source = 'sheet' ou 'csv'."""
    if os.environ.get("SHEET_ID"):
        try:
            sujets, _feuille = lire_sheet()
            for s in sujets:
                if str(s.get("statut", "")).strip() == STATUT_A_PRODUIRE:
                    return _normaliser(s), "sheet"
            raise RuntimeError("Aucun sujet avec le statut « à produire » dans le Google Sheet.")
        except ImportError as e:
            print(f"[sujets] gspread indisponible ({e}) : repli sur le CSV local.")
        except Exception as e:
            print(f"[sujets] Lecture du Sheet impossible ({e}) : repli sur le CSV local.")

    sujets = lire_csv()
    for s in sujets:
        if s.get("statut", "").strip() == STATUT_A_PRODUIRE:
            return _normaliser(s), "csv"
    raise RuntimeError("Aucun sujet avec le statut « à produire » dans topics.csv.")


def _normaliser(s):
    return {
        "nom": s["nom"],
        "nom_latin": s.get("nom_latin", ""),
        "page_wikipedia": s.get("page_wikipedia") or s["nom"],
        "duree": int(s.get("duree") or 8),
        "angle": s.get("angle", "survie"),
        "tension": int(s.get("tension") or 3),
        "statut": s.get("statut", ""),
    }


def marquer_publie(sujet, source="csv"):
    """Marque le sujet comme publié (statut + date du jour)."""
    aujourd_hui = date.today().isoformat()
    if source == "sheet" and os.environ.get("SHEET_ID"):
        try:
            _sujets, feuille = lire_sheet()
            cellules = feuille.findall(sujet["nom"])
            for c in cellules:
                feuille.update_cell(c.row, COLONNES.index("statut") + 1, STATUT_PUBLIE)
                feuille.update_cell(c.row, COLONNES.index("date_publi") + 1, aujourd_hui)
            return
        except Exception as e:
            print(f"[sujets] Mise à jour du Sheet impossible ({e}) : mise à jour du CSV local.")

    path = _csv_path()
    sujets = lire_csv(path)
    modifie = False
    for s in sujets:
        if s.get("nom") == sujet["nom"] and s.get("statut", "").strip() == STATUT_A_PRODUIRE:
            s["statut"] = STATUT_PUBLIE
            s["date_publi"] = aujourd_hui
            modifie = True
            break
    if modifie:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLONNES)
            w.writeheader()
            for s in sujets:
                w.writerow({c: s.get(c, "") for c in COLONNES})
