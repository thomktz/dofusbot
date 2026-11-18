"""Spell config from the game's own spell tooltips (no external database).

Hover a spell in the spell bar or open its details, capture it, and OCR the tooltip text:

    python -m dofus_brain.skills.spells tooltip.png --key 1 [--class cra] [--crop x,y,w,h]

parses PA cost, range, line / line of sight, casts per turn / per target, cooldown and
damage, and writes the spell into config/spells_<class>.json (merged by key). The fight
solver reads that file (fight.spells_file) in place of the placeholder table.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ..textnorm import norm

NUM = r"(\d{1,3})"


def _int(rx: str, t: str) -> int | None:
    m = re.search(rx, t)
    return int(m.group(1)) if m else None


ELEMENT_WORDS = {"neutre": "neutral", "neutral": "neutral", "terre": "earth", "earth": "earth", "feu": "fire",
                 "fire": "fire", "eau": "water", "water": "water", "air": "air"}
ZONE_WORDS = {"croix": "cross", "cross": "cross", "cercle": "circle", "circle": "circle", "ligne": "line",
              "line": "line", "cone": "cone", "case": "single", "cell": "single", "monocible": "single"}
NUMBER_WORDS = {"une": 1, "un": 1, "one": 1, "deux": 2, "two": 2, "trois": 3, "three": 3, "quatre": 4, "four": 4}
# lines that carry no game data (or that the base parser already handled)
KNOWN_LINE = re.compile(
    r"(\bpa\b|\bap\b|cout|cost|portee|range|\bpo\b|ligne de vue|line of sight|lancers?|casts?|par cible|per target|"
    r"relance|cooldown|recast|interval|en ligne|straight line|modifiable|niveau|level|critique|critical|"
    r"dommages?|degats?|damage|zone|area|pousse|repousse|push|attire|pull|retrait|vol de|\bpm\b|\bmp\b|"
    r"teleport|bond|portee \+|\+\s*\d+\s*(?:po|portee|range)|tours?|turns?|case libre|free cell|empty cell)")


def parse_effects(text_lines: list[str]) -> tuple[dict | None, list[dict], list[str]]:
    """(zone, effects, unparsed lines) from normalised tooltip lines (all but the name)."""
    zone = None
    effects: list[dict] = []
    unparsed: list[str] = []
    for t in text_lines:
        hit = False
        for m in re.finditer(rf"(?:dommages?|degats?)\s+(\w+)\s*:?\s*{NUM}\s*(?:a|à|-)\s*{NUM}", t):
            if m.group(1) in ELEMENT_WORDS:
                effects.append({"kind": "damage", "element": ELEMENT_WORDS[m.group(1)],
                                "min": int(m.group(2)), "max": int(m.group(3))})
                hit = True
        for m in re.finditer(rf"(\w+)\s+damage\s*:?\s*{NUM}\s*(?:to|-)\s*{NUM}", t):
            if m.group(1) in ELEMENT_WORDS:
                effects.append({"kind": "damage", "element": ELEMENT_WORDS[m.group(1)],
                                "min": int(m.group(2)), "max": int(m.group(3))})
                hit = True
        if (m := re.search(rf"(?:repousse|pousse|push(?:es)? back|pushback)\D*{NUM}", t)):
            effects.append({"kind": "push", "value": int(m.group(1))}); hit = True
        if (m := re.search(rf"(?:attire|pull(?:s)?)\D*{NUM}", t)):
            effects.append({"kind": "pull", "value": int(m.group(1))}); hit = True
        if (m := re.search(rf"(?:-|retrait de|vol de|removes?)\s*{NUM}\s*(pm|mp|pa|ap)\b", t)):
            kind = "mp_remove" if m.group(2) in ("pm", "mp") else "ap_remove"
            effects.append({"kind": kind, "value": int(m.group(1))}); hit = True
        if (m := re.search(rf"\+\s*{NUM}\s*(?:po|portee|range)(?:.*?{NUM}\s*(?:tours?|turns?))?", t)):
            effects.append({"kind": "range_buff", "value": int(m.group(1)),
                            "turns": int(m.group(2)) if m.group(2) else 1}); hit = True
        if re.search(r"teleporte|teleports?\b", t):
            effects.append({"kind": "teleport"}); hit = True
        if (m := re.search(r"zone\s*:?\s*(?:en\s+|in\s+a\s+)?(\w+)(?:\s+de)?(?:\s+(?:rayon|taille|size|of)\s*)?\s*(\d+|\w+)?", t)) or \
                (m := re.search(r"(croix|cercle|cone|ligne|cross|circle|line)\s+(?:de|of)\s+(?:rayon\s+|taille\s+|size\s+)?(\d+|\w+)(?:\s+cases?|\s+cells?)?", t)):
            shape = ZONE_WORDS.get(m.group(1))
            size_word = m.group(2) or "0"
            size = int(size_word) if size_word.isdigit() else NUMBER_WORDS.get(size_word)
            if shape and size is not None:
                zone = {"shape": shape, "size": 0 if shape == "single" else size}
                hit = True
        if not hit and not KNOWN_LINE.search(t):
            unparsed.append(t)
        elif not hit and re.search(r"zone|area|dommages?|degats?|damage|pousse|push|retrait|removes?", t) \
                and not re.search(r"critique|critical", t):
            unparsed.append(t)  # looks like an effect we could not read: review, never guess
    return zone, effects, unparsed


def parse_spell_tooltip(lines: list[str]) -> dict:
    """OCR lines of one spell tooltip -> spell dict (only the fields that were found).
    French and English client texts. Area shape and effects are read too; any line that
    looks like game data but could not be parsed goes to out["review"] (never guessed)."""
    clean = [" ".join(l.split()) for l in lines if l and l.strip()]
    if not clean:
        raise ValueError("empty tooltip")
    out: dict = {"name": clean[0]}
    t = "\n".join(norm(l) for l in clean)
    pa = _int(rf"{NUM}\s*(?:pa|ap)\b", t) or _int(rf"(?:cout|cost)\s*:?\s*{NUM}", t)
    if pa is not None:
        out["pa"] = pa
    m = re.search(rf"(?:portee|range|po)\s*:?\s*{NUM}\s*(?:-|a|to|à)\s*{NUM}", t) or \
        re.search(rf"{NUM}\s*(?:-|a|to)\s*{NUM}\s*(?:po|range)\b", t)
    if m:
        out["min_range"], out["max_range"] = int(m.group(1)), int(m.group(2))
    else:
        r = _int(rf"(?:portee|range)\s*:?\s*{NUM}", t) or _int(rf"{NUM}\s*po\b", t)
        if r is not None:
            out["min_range"] = out["max_range"] = r
    if re.search(r"portee (?:non )?modifiable|range (?:can(?:not|'t)? be )?(?:modified|boosted)|modifiable range", t):
        out["range_modifiable"] = not re.search(r"non modifiable|cannot be|can't be|not modifiable", t)
    if re.search(r"en ligne|in a (?:straight )?line|linear", t):
        out["line"] = True
    if re.search(r"(?:pas de|sans|no|without) ligne de vue|no line of sight|without line of sight", t):
        out["los"] = False
    elif re.search(r"ligne de vue|line of sight", t):
        out["los"] = True
    v = _int(rf"(?:lancers? par tour|casts? per turn|uses? per turn)\s*:?\s*{NUM}", t)
    if v is not None:
        out["per_turn"] = v
    v = _int(rf"(?:par cible|per target)\s*:?\s*{NUM}", t)
    if v is not None:
        out["per_target"] = v
    v = _int(rf"(?:intervalle de relance|relance|cooldown|recast interval)\s*:?\s*{NUM}", t)
    if v is not None:
        out["cooldown"] = v
    dmg = re.findall(rf"(?:dommages?|degats?|damage)\s*(?:\w+\s*)?:?\s*{NUM}\s*(?:a|à|to|-)\s*{NUM}", t)
    if dmg:
        out["damage"] = round(sum((int(a) + int(b)) / 2 for a, b in dmg), 1)
    elif (d := _int(rf"(?:dommages?|degats?|damage)\s*(?:\w+\s*)?:?\s*{NUM}", t)) is not None:
        out["damage"] = float(d)
    if re.search(r"teleport|teleporte|bond\b", t):
        out["kind"] = "teleport"
    zone, effects, unparsed = parse_effects([norm(l) for l in clean[1:]])
    if zone:
        out["zone"] = zone
    if effects:
        out["effects"] = effects
    if unparsed:
        out["review"] = unparsed
    return out


def spells_path(config_dir: Path, character_class: str) -> Path:
    return Path(config_dir) / f"spells_{character_class}.json"


def load_spells_file(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return [s for s in data.get("spells", []) if isinstance(s, dict)]


def update_spells_file(path: Path, spell: dict, key: str, character_class: str) -> dict:
    """Merge `spell` into the file under its bar key; marks it verified (read from the game)."""
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "_comment": "Spells read from in-game tooltips (python -m dofus_brain.skills.spells). "
                    "verified=false entries are placeholders.", "class": character_class, "spells": []}
    spells = [s for s in data.get("spells", []) if str(s.get("key")) != str(key)]
    entry = {"key": str(key), **spell, "verified": True}
    spells.append(entry)
    data["spells"] = sorted(spells, key=lambda s: str(s.get("key")))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def main(argv: list[str] | None = None) -> int:
    from ..config import load_json
    from ..ocr import crop, load_image, make_ocr, parse_rect, text_of, upscale
    from ..paths import config_dir

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--key", required=True, help="spell bar key of this spell (1..9, 0)")
    ap.add_argument("--class", dest="cls", default="cra")
    ap.add_argument("--crop", help="x,y,w,h of the tooltip (fractions or pixels)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    img = load_image(a.image)
    if a.crop:
        img = crop(img, parse_rect(a.crop))
    ocr = make_ocr(load_json("regions").get("ocr", {}))
    lines = text_of(ocr.recognize(upscale(img, 2.0))).splitlines()
    spell = parse_spell_tooltip(lines)
    print(json.dumps(spell, indent=2, ensure_ascii=False))
    if spell.get("review"):
        print("NEEDS REVIEW (not used by the solver until you fix the entry):")
        for ln in spell["review"]:
            print("  -", ln)
    if not a.dry_run:
        p = spells_path(config_dir(), a.cls)
        update_spells_file(p, spell, a.key, a.cls)
        print(f"written to {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
