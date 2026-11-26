#!/usr/bin/env python3
"""Build the data file for one chapter of the Bible, Hebrew or Greek.

Run it:      python build.py Ecclesiastes 1        (or: python build.py Ecc 1)
             python build.py John 1
Then view:   python -m http.server    and open http://localhost:8000

What it does
  1. Downloads the open source files into ./data (first run only).
  2. Reads every word of the chapter in its original language, with its
     dictionary form and grammar.
  3. Reads the English verses, and which words in them are Jesus speaking.
  4. Counts how often each dictionary form appears in the chapter.
  5. Writes chapters/<book>-<chapter>.json, one verse per line.
  6. Adds the chapter to chapters.json, the list the reader page shows.

Nothing here is guessed. Every field comes straight from the sources:
  - Old Testament words: MACULA Hebrew Linguistic Datasets (CC BY 4.0)
    https://github.com/Clear-Bible/macula-hebrew/
  - New Testament words: MACULA Greek Linguistic Datasets, SBLGNT edition
    (CC BY 4.0)   https://github.com/Clear-Bible/macula-greek/
  - English: Berean Standard Bible (public domain), the publisher's own USFM
    files   https://github.com/BSB-publishing/bsb2usfm

Standard library only. Python 3.10 or newer.
"""

import argparse
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

HEBREW_URL = "https://raw.githubusercontent.com/Clear-Bible/macula-hebrew/main/WLC/lowfat/"
GREEK_URL = "https://raw.githubusercontent.com/Clear-Bible/macula-greek/main/SBLGNT/lowfat/"
ENGLISH_URL = "https://github.com/BSB-publishing/bsb2usfm/releases/latest/download/BSB_usfm.zip"

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"                 # downloads, safe to delete
CHAPTERS = HERE / "chapters"         # what this script writes
MANIFEST = HERE / "chapters.json"    # the list of chapters built so far

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

# Words that carry meaning. Everything else ("and", "the", "in") is grammar
# glue, which repeats constantly and would drown out the interesting patterns.
CONTENT = {"noun", "verb", "adjective", "adverb"}

# The Old Testament. Left: the abbreviation in MACULA Hebrew's file names,
# copied from the source, quirks included ("HOS"). In capitals it is also the
# book's code in the English files.
OLD_TESTAMENT = [
    ("Gen", "Genesis"), ("Exo", "Exodus"), ("Lev", "Leviticus"), ("Num", "Numbers"),
    ("Deu", "Deuteronomy"), ("Jos", "Joshua"), ("Jdg", "Judges"), ("Rut", "Ruth"),
    ("1Sa", "1 Samuel"), ("2Sa", "2 Samuel"), ("1Ki", "1 Kings"), ("2Ki", "2 Kings"),
    ("1Ch", "1 Chronicles"), ("2Ch", "2 Chronicles"), ("Ezr", "Ezra"),
    ("Neh", "Nehemiah"), ("Est", "Esther"), ("Job", "Job"), ("Psa", "Psalms"),
    ("Pro", "Proverbs"), ("Ecc", "Ecclesiastes"), ("Sng", "Song of Solomon"),
    ("Isa", "Isaiah"), ("Jer", "Jeremiah"), ("Lam", "Lamentations"), ("Ezk", "Ezekiel"),
    ("Dan", "Daniel"), ("HOS", "Hosea"), ("Jol", "Joel"), ("Amo", "Amos"),
    ("Oba", "Obadiah"), ("Jon", "Jonah"), ("Mic", "Micah"), ("Nam", "Nahum"),
    ("Hab", "Habakkuk"), ("Zep", "Zephaniah"), ("Hag", "Haggai"), ("Zec", "Zechariah"),
    ("Mal", "Malachi"),
]

# The New Testament. Left: the book's code, used in the Greek data's verse
# references and in the English files. Right: its file name in MACULA Greek.
NEW_TESTAMENT = [
    ("MAT", "Matthew", "matthew"), ("MRK", "Mark", "mark"), ("LUK", "Luke", "luke"),
    ("JHN", "John", "john"), ("ACT", "Acts", "acts"), ("ROM", "Romans", "romans"),
    ("1CO", "1 Corinthians", "1corinthians"), ("2CO", "2 Corinthians", "2corinthians"),
    ("GAL", "Galatians", "galatians"), ("EPH", "Ephesians", "ephesians"),
    ("PHP", "Philippians", "philippians"), ("COL", "Colossians", "colossians"),
    ("1TH", "1 Thessalonians", "1thessalonians"), ("2TH", "2 Thessalonians", "2thessalonians"),
    ("1TI", "1 Timothy", "1timothy"), ("2TI", "2 Timothy", "2timothy"),
    ("TIT", "Titus", "titus"), ("PHM", "Philemon", "philemon"), ("HEB", "Hebrews", "hebrews"),
    ("JAS", "James", "james"), ("1PE", "1 Peter", "1peter"), ("2PE", "2 Peter", "2peter"),
    ("1JN", "1 John", "1john"), ("2JN", "2 John", "2john"), ("3JN", "3 John", "3john"),
    ("JUD", "Jude", "jude"), ("REV", "Revelation", "revelation"),
]

# MACULA Greek names parts of speech in short form. These are the long forms
# MACULA Hebrew uses, so the rest of the code can treat both alike.
GREEK_POS = {
    "noun": "noun", "verb": "verb", "adj": "adjective", "adv": "adverb",
    "det": "article", "conj": "conjunction", "pron": "pronoun", "prep": "preposition",
    "ptcl": "particle", "num": "number", "intj": "interjection",
}


# ---- Which chapter? ----------------------------------------------------------

def all_books() -> list:
    books = []
    for abbr, name in OLD_TESTAMENT:
        books.append({"number": len(books) + 1, "code": abbr.upper(), "abbr": abbr,
                      "name": name, "lang": "he"})
    for code, name, file in NEW_TESTAMENT:
        books.append({"number": len(books) + 1, "code": code, "abbr": code.title(),
                      "name": name, "lang": "grc", "file": file})
    return books


def find_book(name: str) -> dict:
    """Turn 'Ecclesiastes', 'ecc', 'John' or '1 Samuel' into everything we need."""
    def squash(text):
        return re.sub(r"[^a-z0-9]", "", text.lower())

    wanted = squash(name)
    matches = []
    for book in all_books():
        if wanted in {squash(book["code"]), squash(book["name"])}:
            return book
        if len(wanted) >= 3 and squash(book["name"]).startswith(wanted):
            matches.append(book)
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(f"Cannot tell which book '{name}' is. Try a name like "
                     f"Genesis, John or \"1 Samuel\".")


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


def read_words(book: dict, chapter: int) -> list:
    """Return every <w> element of the chapter, in reading order.

    MACULA stores the text as a syntax tree, so the file is not in reading
    order. Each <w> has an id that is, and sorting by it restores the text.

    In Hebrew one <w> is one *piece* of a written word. Hebrew fuses small
    words onto big ones: "and he said" is a single word with two pieces.
    In Greek one <w> is simply one word.
    """
    if book["lang"] == "he":
        # One file per chapter: <book number>-<abbreviation>-<chapter>-lowfat.xml
        name = f"{book['number']:02d}-{book['abbr']}-{chapter:03d}-lowfat.xml"
        root = ET.parse(download(HEBREW_URL + name, DATA / name)).getroot()
        words = list(root.iter("w"))
    else:
        # One file per book. Keep the words whose reference is in our chapter.
        name = f"{book['number'] - len(OLD_TESTAMENT):02d}-{book['file']}.xml"
        root = ET.parse(download(GREEK_URL + name, DATA / name)).getroot()
        start = f"{book['code']} {chapter}:"
        words = [w for w in root.iter("w") if (w.get("ref") or "").startswith(start)]
    if not words:
        raise SystemExit(f"{book['name']} has no chapter {chapter} in the data.")
    return sorted(words, key=lambda w: w.get(XML_ID))


# Lines in the English files that are headings, not Bible text.
HEADINGS = ("\\id", "\\h", "\\toc", "\\mt", "\\s", "\\ms", "\\r", "\\qa")
USFM_TOKEN = re.compile(r"\\[a-z]+[0-9]*\*?|[^\\]+")


def read_english(path: Path, book: dict, chapter: int) -> dict:
    """Return {verse number: (text, red)} for the chapter.

    `red` lists the stretches of the text that are Jesus speaking, as
    [start, end] character positions. Printed Bibles set those in red.

    The file is USFM, the standard markup for Bible text. The few markers
    that matter here:
        \\c 1          chapter 1 starts
        \\v 3          verse 3 starts
        \\wj ... \\wj*   words of Jesus
        \\f ... \\f*     a footnote, which we leave out
    Everything else (\\p paragraphs, \\q1 poetry lines ...) only shapes layout.
    """
    with zipfile.ZipFile(path) as archive:
        text = archive.read(f"{book['code']}.usfm").decode("utf-8")

    verses = {}
    here = verse = None          # chapter and verse we are inside
    letters, red = [], []        # the current verse so far
    in_note = 0
    jesus = False

    def finish_verse():
        nonlocal letters, red
        if verse is not None and here == chapter:
            line = "".join(letters).rstrip()
            verses[verse] = (line, [[a, min(b, len(line))] for a, b in red if a < len(line)])
        letters, red = [], []

    def add(piece):
        piece = re.sub(r"\s+", " ", piece)
        # The files have stray spaces around some punctuation ("goodbye ." and
        # "“No, ” he said"). Close those gaps. Nothing else is changed.
        piece = re.sub(r" ([.,;:!?”’)])", r"\1", piece)
        piece = re.sub(r"([“‘(]) ", r"\1", piece)
        if letters and letters[-1] == " " and piece[:1] in tuple(".,;:!?”’)"):
            letters.pop()
            for stretch in red:
                stretch[1] = min(stretch[1], len(letters))
        if not letters or letters[-1] == " " or letters[-1] in "“‘(":
            piece = piece.lstrip(" ")
        if not piece:
            return
        start = len(letters)
        letters.extend(piece)
        if jesus:
            while start < len(letters) and letters[start] == " ":
                start += 1
            if red and all(c == " " for c in letters[red[-1][1]:start]):
                red[-1][1] = len(letters)        # carry on the stretch before
            else:
                red.append([start, len(letters)])

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("\\c "):
            finish_verse()
            here, verse, jesus, in_note = int(line.split()[1]), None, False, 0
            continue
        if line.startswith(HEADINGS):
            continue
        if letters and letters[-1] != " ":
            letters.append(" ")                  # a new line continues the verse
        tokens = USFM_TOKEN.findall(line)
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if not token.startswith("\\"):
                # The space right after an opening marker ("\add the") only
                # ends the marker's name. It is not part of the text.
                if i and tokens[i - 1].startswith("\\") and not tokens[i - 1].endswith("*"):
                    token = token[1:] if token.startswith(" ") else token
                if not in_note and verse is not None:
                    add(token)
            elif token in ("\\f", "\\x"):
                in_note += 1
            elif token in ("\\f*", "\\x*"):
                in_note = max(0, in_note - 1)
            elif in_note:
                pass
            elif token == "\\wj":
                jesus = True
            elif token == "\\wj*":
                jesus = False
            elif token == "\\v" and i + 1 < len(tokens):
                number, rest = re.match(r"\s*(\d+)\s?(.*)", tokens[i + 1], re.S).groups()
                finish_verse()
                verse = int(number)
                add(rest)
                i += 1
            i += 1
    finish_verse()
    return verses


def subjects(w, by_id: dict) -> list:
    """Who or what does the action of this verb, if MACULA says.

    The 'frame' attribute lists the verb's participants by word id.
    A0 is the one doing or being; we keep that and leave the rest for later.
    Hebrew example: 'A0:010010010031; A1:010010010052;010010010072;'
    Greek example:  'A0:n43001003001'
    """
    out = []
    label = None
    for token in re.split(r"[;\s]+", w.get("frame") or ""):
        if ":" in token:
            label, token = token.split(":", 1)
        if label != "A0" or not token:
            continue
        target = by_id.get(token)
        if target is None:
            target = by_id.get("o" + token)      # Hebrew ids drop their first letter here
        if target is not None:
            out.append({
                "id": target.get(XML_ID),
                "text": target.text or "",
                "english": target.get("english") or "",
                "verse": verse_number(target),
            })
    return out


def spot_english(w, lang: str) -> str:
    """The English for this word in this spot.

    Both datasets carry a contextual English word for most pieces. Where one
    is missing we fall back to the dataset's other gloss. Two things have no
    English at all and stay empty: the Hebrew object marker, and a Greek
    "the" that English leaves out (the fallback gloss is "-").
    """
    if w.get("english"):
        return w.get("english")
    if lang == "he":
        if w.get("class") == "om":
            return ""
        return (w.get("gloss") or "").replace(".", " ")
    gloss = (w.get("gloss") or "").strip()
    return "" if gloss in ("", "-") else gloss


def verse_number(w) -> int:
    """'GEN 1:3!2' -> 3"""
    return int(w.get("ref").split("!")[0].split(":")[1])


def word_number(w) -> int:
    """'GEN 1:3!2' -> 2  (second written word of the verse)"""
    return int(w.get("ref").split("!")[1])


def counting_key(w, lang: str) -> str:
    """What makes two words "the same word" when we count repeats.

    Hebrew: the Strong's number. A dictionary form can be shared by two
    different words (the noun "light" and the verb "give light" are spelled
    alike), and the Strong's number tells them apart.
    Greek: the dictionary form plus the part of speech. Strong's numbers are
    no use here, because they split one verb ("to be") across several numbers.
    """
    if lang == "he":
        return w.get("strongnumberx")
    return f"{w.get('lemma')}|{w.get('class')}"


def part_of_speech(w, lang: str) -> str:
    return w.get("pos") if lang == "he" else GREEK_POS.get(w.get("class"), w.get("class"))


# ---- The build ---------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Build one chapter for the reader.")
    parser.add_argument("book", help='book name or abbreviation, e.g. Ecclesiastes, Ecc, John, "1 Samuel"')
    parser.add_argument("chapter", type=int)
    args = parser.parse_args()

    book = find_book(args.book)
    chapter = args.chapter
    lang = book["lang"]
    cid = chapter_id(book, chapter)

    words = read_words(book, chapter)
    english = read_english(download(ENGLISH_URL, DATA / "BSB_usfm.zip"), book, chapter)
    by_id = {w.get(XML_ID): w for w in words}

    counts = Counter(counting_key(w, lang) for w in words
                     if part_of_speech(w, lang) in CONTENT)
    glosses = defaultdict(Counter)
    for w in words:
        if w.get("english"):
            glosses[counting_key(w, lang)][w.get("english")] += 1

    verses = defaultdict(lambda: defaultdict(list))
    for w in words:
        key = counting_key(w, lang)
        piece = {
            "id": w.get(XML_ID),
            # A few Hebrew pieces have no letters of their own: after a
            # one-letter preposition, "the" drops out and leaves only its vowel.
            "text": w.text or "",
            "after": w.get("after") or "",        # space, hyphen or punctuation
            "english": spot_english(w, lang),     # English for this spot
            "lemma": w.get("lemma"),              # dictionary form
            "strong": w.get("strongnumberx") if lang == "he" else w.get("strong"),
            "key": key,                           # what repeats are counted by
            "translit": w.get("transliteration") or "",
            "pos": part_of_speech(w, lang),       # part of speech
            "class": w.get("class"),              # finer part of speech
            "type": w.get("type") or "",          # Hebrew verb form, noun type ...
            "stem": w.get("stem") or "",          # Hebrew verb pattern
            "tense": w.get("tense") or "",        # Greek
            "voice": w.get("voice") or "",        # Greek
            "mood": w.get("mood") or "",          # Greek
            "case": w.get("case") or "",          # Greek
            "degree": w.get("degree") or "",      # Greek
            "person": w.get("person") or "",
            "gender": w.get("gender") or "",
            "number": w.get("number") or "",
            "state": w.get("state") or "",        # Hebrew
            "count": counts.get(key, 0),          # times in this chapter
            "lemma_english": (
                glosses[key].most_common(1)[0][0] if glosses[key] else ""
            ),
            "subject": subjects(w, by_id),
        }
        # Drop empty fields to keep the file small and easy to read.
        piece = {k: v for k, v in piece.items() if v not in ("", [], 0, None)}
        verses[verse_number(w)][word_number(w)].append(piece)

    records = []
    for v in sorted(verses):
        text, red = english.get(v, ("", []))
        record = {"verse": v, "english": text}
        if red:
            record["red"] = red                   # where Jesus is speaking
        record["words"] = [verses[v][n] for n in sorted(verses[v])]
        records.append(record)

    # One verse per line: valid JSON, and easy to diff or skim.
    CHAPTERS.mkdir(exist_ok=True)
    out = CHAPTERS / f"{cid}.json"
    lines = ",\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    out.write_text("[\n" + lines + "\n]\n", encoding="utf-8")

    # Add the chapter to the list the page reads, in Bible order. "latest" is
    # the chapter the page opens on when the link does not name one.
    manifest = (json.loads(MANIFEST.read_text(encoding="utf-8"))
                if MANIFEST.exists() else {"chapters": []})
    entry = {"id": cid, "book": book["name"], "chapter": chapter, "lang": lang,
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
          f"{len(words)} pieces, "
          f"{sum(1 for r in records if r.get('red'))} verses with words of Jesus\n")

    # Hebrew and English Bibles number some verses differently (many psalms,
    # for one). This script pairs verses by number, so warn when that is unsafe.
    if len(english) != len(records):
        print(f"WARNING: the original has {len(records)} verses here and the English "
              f"has {len(english)}.\nThe two number this chapter differently, so "
              f"some English lines may sit beside the wrong verse.\n")

    print("Verse 1, piece by piece")
    for w in words:
        if verse_number(w) != verse_number(words[0]):
            break
        grammar = " ".join(filter(None, [
            part_of_speech(w, lang), w.get("type"), w.get("stem"),
            w.get("tense"), w.get("voice"), w.get("mood"), w.get("case")]))
        print(f"  {(w.text or '-'):<12} lemma {w.get('lemma'):<10} "
              f"{(w.get('english') or '-'):<10} {grammar}")

    print("\nWords that repeat (3 or more times)")
    for key, n in counts.most_common(24):
        if n < 3:
            break
        lemma = next(w.get("lemma") for w in words if counting_key(w, lang) == key)
        gloss = glosses[key].most_common(1)[0][0] if glosses[key] else "-"
        print(f"  {n:>3}  {lemma:<10} {gloss}")

    print(f"\nwrote chapters/{out.name} and updated {MANIFEST.name}")
    print(f"next: python explain.py {book['abbr']} {chapter} --sheet")


if __name__ == "__main__":
    main()
