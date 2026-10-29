"""Faits vérifiables : Wikipédia FR + GBIF.

Les faits envoyés à Gemini sont limités à 9 000 caractères au total
(voir config/settings.yaml) pour économiser les tokens.
Aucune clé API nécessaire pour ces deux sources.
"""

USER_AGENT = "Varus/1.0 (robot documentaire Territoire Brut ; contact via YouTube)"
TIMEOUT = 30


def _http_get(url, params=None):
    """GET avec requests (import paresseux : le dry-run reste possible
    sans dépendances installées)."""
    import requests
    return requests.get(url, params=params, timeout=TIMEOUT,
                        headers={"User-Agent": USER_AGENT})


def wikipedia_facts(titre, lang="fr", max_chars=9000):
    """Extrait texte de l'article Wikipédia. Renvoie '' si introuvable."""
    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {"action": "query", "prop": "extracts", "explaintext": 1,
              "titles": titre, "format": "json", "redirects": 1}
    r = _http_get(url, params)
    r.raise_for_status()
    pages = r.json()["query"]["pages"]
    page = next(iter(pages.values()))
    if "missing" in page:
        return ""
    return (page.get("extract") or "")[:max_chars]


def gbif_facts(nom_scientifique):
    """Classification GBIF à partir du nom latin. Renvoie '' si introuvable."""
    r = _http_get("https://api.gbif.org/v1/species/match",
                  {"name": nom_scientifique})
    r.raise_for_status()
    d = r.json()
    if not d.get("scientificName"):
        return ""
    rangs = " > ".join(x for x in [d.get("kingdom"), d.get("phylum"),
                                   d.get("class"), d.get("order"),
                                   d.get("family"), d.get("genus")] if x)
    return (f"Classification GBIF : {rangs} "
            f"({d.get('scientificName')}). Statut taxonomique : "
            f"{d.get('status', 'inconnu')}.")


def fetch_facts(sujet, max_chars=9000):
    """Faits combinés Wikipédia + GBIF, plafonnés à max_chars caractères."""
    morceaux = []
    wiki = wikipedia_facts(sujet["page_wikipedia"])
    if wiki:
        morceaux.append(f"--- Wikipédia ({sujet['page_wikipedia']}) ---\n{wiki}")
    if sujet.get("nom_latin"):
        gbif = gbif_facts(sujet["nom_latin"])
        if gbif:
            morceaux.append(f"--- GBIF ---\n{gbif}")
    faits = "\n\n".join(morceaux)
    return faits[:max_chars]
