"""Champion name normalization shared by ground-truth loading and backtests.

Riot payloads use internal ids ("MonkeyKing", "Kaisa", "RekSai"); esports
sources use display names ("Wukong", "Kai'Sa", "Rek'Sai"). champ_key()
squashes both to a canonical join key: lowercase alphanumerics plus an
alias map for the few names where squashing isn't enough.
"""
import re

# squashed-form -> canonical key (Riot internal id, squashed, wins)
_ALIASES = {
    "wukong": "monkeyking",          # display name vs Riot id
    "nunuwillump": "nunu",           # "Nunu & Willump"
    "renataglasc": "renata",
    "fiddle": "fiddlesticks",        # occasional esports shorthand
}

_SQUASH = re.compile(r"[^a-z0-9]+")


def champ_key(name: str) -> str:
    squashed = _SQUASH.sub("", (name or "").lower())
    return _ALIASES.get(squashed, squashed)
