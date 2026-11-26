#!/usr/bin/env python3
"""Build the data file for one chapter of the Hebrew Bible.

Run it:      python build.py Ecclesiastes 1        (or: python build.py Ecc 1)
Then view:   python -m http.server    and open http://localhost:8000

What it does
  1. Downloads two open files into ./data (first run only).
  2. Reads every Hebrew word of the chapter with its dictionary form and grammar.
  3. Counts how often each dictionary form appears in the chapter.
  4. Writes chapters/<book>-<chapter>.json, one verse per line.
  5. Adds the chapter to chapters.json, the list the reader page shows.

Nothing here is guessed. Every field comes straight from the two sources:
  - MACULA Hebrew Linguistic Datasets (CC BY 4.0)
    https://github.com/Clear-Bible/macula-hebrew/
  - Berean Standard Bible (public domain), via scrollmapper/bible_databases

Standard library only. Python 3.10 or newer.
"""

import argparse
import csv
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

MACULA_URL = "https://raw.githubusercontent.com/Clear-Bible/macula-hebrew/main/WLC/lowfat/"
BSB_URL = ("https://raw.githubusercontent.com/scrollmapper/bible_databases/master/"
           "formats/csv/BSB.csv")

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"                 # downloads, safe to delete
CHAPTERS = HERE / "chapters"         # what this script writes
MANIFEST = HERE / "chapters.json"    # the list of chapters built so far

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

# Words that carry meaning. Everything else ("and", "the", "in") is grammar
# glue, which repeats constantly and would drown out the interesting patterns.
CONTENT = {"noun", "verb", "adjective", "adverb"}

# The 39 books, in the order MACULA numbers its files (01-Gen ... 39-Mal).
# Left: the abbreviation in MACULA's file names. Right: the book's name in the
# Berean CSV. Both are copied from the sources, quirks included ("HOS").
BOOKS = [
    ("Gen", "Genesis"), ("Exo", "Exodus"), ("Lev", "Leviticus"), ("Num", "Numbers"),
    ("Deu", "Deuteronomy"), ("Jos", "Joshua"), ("Jdg", "Judges"), ("Rut", "Ruth"),
    ("1Sa", "I Samuel"), ("2Sa", "II Samuel"), ("1Ki", "I Kings"), ("2Ki", "II Kings"),
    ("1Ch", "I Chronicles"), ("2Ch", "II Chronicles"), ("Ezr", "Ezra"),
    ("Neh", "Nehemiah"), ("Est", "Esther"), ("Job", "Job"), ("Psa", "Psalms"),
    ("Pro", "Proverbs"), ("Ecc", "Ecclesiastes"), ("Sng", "Song of Solomon"),
    ("Isa", "Isaiah"), ("Jer", "Jeremiah"), ("Lam", "Lamentations"), ("Ezk", "Ezekiel"),
    ("Dan", "Daniel"), ("HOS", "Hosea"), ("Jol", "Joel"), ("Amo", "Amos"),
    ("Oba", "Obadiah"), ("Jon", "Jonah"), ("Mic", "Micah"), ("Nam", "Nahum"),
    ("Hab", "Habakkuk"), ("Zep", "Zephaniah"), ("Hag", "Haggai"), ("Zec", "Zechariah"),
    ("Mal", "Malachi"),
]


# ---- Which chapter? ----------------------------------------------------------

def find_book(name: str) -> dict:
    """Turn 'Ecclesiastes', 'ecc' or '1 Samuel' into everything we need to know."""
    def squash(text):
        return re.sub(r"[^a-z0-9]", "", text.lower())

    wanted = squash(name)
    matches = []
    for number, (abbr, csv_name) in enumerate(BOOKS, 1):
        shown = csv_name.replace("II ", "2 ").replace("I ", "1 ")   # "1 Samuel"
        book = {"number": number, "abbr": abbr, "csv_name": csv_name, "name": shown}
        keys = {squash(abbr), squash(csv_name), squash(shown)}
        if wanted in keys:
            return book
        if len(wanted) >= 3 and squash(shown).startswith(wanted):
            matches.append(book)
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(f"Cannot tell which book '{name}' is. Try a name like "
                     f"Genesis, Ecclesiastes or \"1 Samuel\".")


def chapter_id(book: dict, chapter: int) -> str:
    """'Ecclesiastes', 1 -> 'ecclesiastes-1'. Used for file names and links."""
    return re.sub(r"[^a-z0-9]+", "-", book["name"].lower()) + f"-{chapter}"


# ---- Reading the sources -----------------------------------------------------

def download(url: str, dest: Path) -> Path:
    """Fetch a file once and keep it in ./data."""
    if not dest.exists():
        DATA.mkdir(exist_ok=True)
        print(f"downloading {dest.name} ...")
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception as error:
            raise SystemExit(f"Could not download {url}\n{error}\n"
                             f"Does the book have that many chapters?")
    return dest


def read_english(path: Path, book: dict, chapter: int) -> dict:
    """Return {verse number: English text} for the chapter."""
    verses = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["Book"] == book["csv_name"] and int(row["Chapter"]) == chapter:
                verses[int(row["Verse"])] = row["Text"]
    return verses


def read_hebrew(path: Path) -> list:
    """Return every <w> element of the chapter, in reading order.

    MACULA stores the chapter as a syntax tree, so the file is not in reading
    order. Each <w> has an id that is, and sorting by it restores the text.

    One <w> is one *piece* of a written word. Hebrew fuses small words onto
    big ones: "and he said" is written as a single word with two pieces.
    """
    root = ET.parse(path).getroot()
    return sorted(root.iter("w"), key=lambda w: w.get(XML_ID))


def subjects(w, by_id: dict) -> list:
    """Who or what does the action of this verb, if MACULA says.

    The 'frame' attribute lists the verb's participants by word id.
    A0 is the one doing or being; we keep that and leave the rest for later.
    Example: 'A0:010010010031; A1:010010010052;010010010072;'
    """
    out = []
    for label, ids in re.findall(r"(\w+):([\d;\s]+)", w.get("frame") or ""):
        if label != "A0":
            continue
        for word_id in re.findall(r"\d{12}", ids):
            target = by_id.get("o" + word_id)
            if target is not None:
                out.append({
                    "id": target.get(XML_ID),
                    "he": target.text or "",
                    "english": target.get("english") or "",
                    "verse": verse_number(target),
                })
    return out


def spot_english(w) -> str:
    """The English for this piece in this spot.

    A few pieces have no contextual English. For those we fall back to the
    dictionary gloss ("he.created" -> "he created"). The object marker is the
    exception: it has no English equivalent at all, so it stays empty.
    """
    if w.get("english"):
        return w.get("english")
    if w.get("class") == "om":
        return ""
    return (w.get("gloss") or "").replace(".", " ")


def verse_number(w) -> int:
    """'GEN 1:3!2' -> 3"""
    return int(w.get("ref").split("!")[0].split(":")[1])


def word_number(w) -> int:
    """'GEN 1:3!2' -> 2  (second written word of the verse)"""
    return int(w.get("ref").split("!")[1])


# ---- The build ---------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Build one chapter for the reader.")
    parser.add_argument("book", help='book name or abbreviation, e.g. Ecclesiastes, Ecc, "1 Samuel"')
    parser.add_argument("chapter", type=int)
    args = parser.parse_args()

    book = find_book(args.book)
    chapter = args.chapter
    cid = chapter_id(book, chapter)
    # MACULA's file naming: <book number>-<abbreviation>-<chapter>-lowfat.xml
    macula_file = f"{book['number']:02d}-{book['abbr']}-{chapter:03d}-lowfat.xml"

    hebrew = read_hebrew(download(MACULA_URL + macula_file, DATA / macula_file))
    english = read_english(download(BSB_URL, DATA / "BSB.csv"), book, chapter)
    by_id = {w.get(XML_ID): w for w in hebrew}

    # A dictionary form (lemma) can be shared by two different words: the noun
    # "light" and the verb "give light" are spelled alike. The Strong's number
    # tells them apart, so that is what we count by.
    counts = Counter(
        w.get("strongnumberx") for w in hebrew if w.get("pos") in CONTENT
    )
    glosses = defaultdict(Counter)
    for w in hebrew:
        if w.get("english"):
            glosses[w.get("strongnumberx")][w.get("english")] += 1

    verses = defaultdict(lambda: defaultdict(list))
    for w in hebrew:
        strong = w.get("strongnumberx")
        piece = {
            "id": w.get(XML_ID),
            # A few pieces have no letters of their own: after a one-letter
            # preposition, "the" drops out and leaves only its vowel behind.
            "he": w.text or "",
            "after": w.get("after") or "",        # space, hyphen or verse end
            "english": spot_english(w),           # English for this spot
            "lemma": w.get("lemma"),              # dictionary form
            "strong": strong,
            "translit": w.get("transliteration") or "",
            "pos": w.get("pos"),                  # part of speech
            "class": w.get("class"),              # finer part of speech
            "type": w.get("type") or "",          # verb form, noun type ...
            "stem": w.get("stem") or "",
            "person": w.get("person") or "",
            "gender": w.get("gender") or "",
            "number": w.get("number") or "",
            "state": w.get("state") or "",
            "count": counts.get(strong, 0),       # times in this chapter
            "lemma_english": (
                glosses[strong].most_common(1)[0][0] if glosses[strong] else ""
            ),
            "subject": subjects(w, by_id),
        }
        # Drop empty fields to keep the file small and easy to read.
        piece = {k: v for k, v in piece.items() if v not in ("", [], 0, None)}
        verses[verse_number(w)][word_number(w)].append(piece)

    records = [
        {
            "verse": v,
            "english": english.get(v, ""),
            "words": [verses[v][n] for n in sorted(verses[v])],
        }
        for v in sorted(verses)
    ]

    # One verse per line: valid JSON, and easy to diff or skim.
    CHAPTERS.mkdir(exist_ok=True)
    out = CHAPTERS / f"{cid}.json"
    lines = ",\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    out.write_text("[\n" + lines + "\n]\n", encoding="utf-8")

    # Add the chapter to the list the page reads, in Bible order. "latest" is
    # the chapter the page opens on when the link does not name one.
    manifest = (json.loads(MANIFEST.read_text(encoding="utf-8"))
                if MANIFEST.exists() else {"chapters": []})
    entry = {"id": cid, "book": book["name"], "chapter": chapter,
             "order": book["number"] * 1000 + chapter}
    # Keep the other chapters, minus any whose data file has since been deleted.
    others = [c for c in manifest["chapters"]
              if c["id"] != cid and (CHAPTERS / f"{c['id']}.json").exists()]
    manifest = {"latest": cid,
                "chapters": sorted(others + [entry], key=lambda c: c["order"])}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    # ---- A quick look in the terminal ---------------------------------------
    print(f"\n{book['name']} {chapter}: {len(records)} verses, "
          f"{sum(len(r['words']) for r in records)} written words, "
          f"{len(hebrew)} pieces\n")

    # Hebrew and English Bibles number some verses differently (many psalms,
    # for one). This script pairs verses by number, so warn when that is unsafe.
    if len(english) != len(records):
        print(f"WARNING: the Hebrew has {len(records)} verses here and the English "
              f"has {len(english)}.\nThe two number this chapter differently, so "
              f"some English lines will sit beside the wrong Hebrew.\n")

    print("Verse 1, piece by piece")
    for w in hebrew:
        if verse_number(w) != verse_number(hebrew[0]):
            break
        grammar = " ".join(
            filter(None, [w.get("pos"), w.get("type"), w.get("stem")])
        )
        print(f"  {(w.text or '-'):<12} lemma {w.get('lemma'):<10} "
              f"{(w.get('english') or '-'):<10} {grammar}")

    print("\nWords that repeat (3 or more times)")
    for strong, n in counts.most_common(24):
        if n < 3:
            break
        lemma = next(w.get("lemma") for w in hebrew
                     if w.get("strongnumberx") == strong)
        print(f"  {n:>3}  {lemma:<10} {glosses[strong].most_common(1)[0][0]}")

    print(f"\nwrote chapters/{out.name} and updated {MANIFEST.name}")
    print(f"next: python explain.py {book['abbr']} {chapter} --sheet")


if __name__ == "__main__":
    main()
