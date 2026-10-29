"""Exécutions de commandes système bornées dans le temps.

Contexte : le run n°8 a été annulé par le timeout job de 120 min sans
log exploitable, alors que TOUS les appels FFmpeg/ffprobe du pipeline
étaient sans timeout : une commande bloquée (entrée corrompue, boucle
inattendue) pouvait manger tout le budget du job sans jamais échouer
proprement. Chaque appel passe désormais par ici avec un timeout
généreux mais fini ; au-delà, la commande est tuée et une erreur claire
est levée, ce qui déclenche la sauvegarde (checkpoint) au lieu d'un
timeout silencieux du job.

Les timeouts sont des PLAFONDS, pas des objectifs : un run normal reste
très en dessous. Ils sont passés explicitement par chaque appelant.
"""

import subprocess


class CommandeTropLongue(RuntimeError):
    """Une commande système a dépassé son timeout et a été tuée."""


def _decrire(cmd):
    if isinstance(cmd, (list, tuple)):
        return " ".join(str(c) for c in cmd)
    return str(cmd)


def run(cmd, timeout, **kwargs):
    """Équivalent de subprocess.run(check=True) avec timeout obligatoire.

    Lève CommandeTropLongue si le timeout est dépassé (la commande est
    tuée par subprocess). Les autres échecs (code de retour non nul)
    lèvent CalledProcessError comme avant.
    """
    kwargs.setdefault("check", True)
    try:
        return subprocess.run(cmd, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as e:
        raise CommandeTropLongue(
            f"Commande tuée après {timeout}s sans terminer : "
            f"{_decrire(cmd)}") from e


def check_output(cmd, timeout, **kwargs):
    """Équivalent de subprocess.check_output avec timeout obligatoire."""
    try:
        return subprocess.check_output(cmd, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as e:
        raise CommandeTropLongue(
            f"Commande tuée après {timeout}s sans terminer : "
            f"{_decrire(cmd)}") from e
