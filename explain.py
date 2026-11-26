#!/usr/bin/env python3
"""The model step: short notes on what the English hides.

Who does what
  1. This script finds patterns in a chapter's data file. Plain code, no model.
  2. For a verse with a pattern it writes a fact sheet: that verse's rows and
     the pattern, nothing else.
  3. The model explains the pattern in a sentence or two, using only the sheet.
  4. The script checks the answer before keeping it:
       - the model typed no Hebrew (it points at rows instead)
       - every number in the note appears in the sheet
       - every quoted phrase in the note appears in the sheet
  5. Notes that pass are saved as "unreviewed". After you have read one and
     agree with it, change its status to "reviewed" by hand.

The checks catch invented words, numbers and quotes. They cannot catch a note
that reasons badly from true facts. That is what step 5 is for.

Usage (build the chapter first with build.py)
  python explain.py Ecc 1 --sheet               show every fact sheet
  python explain.py Ecc 1 --sheet --verses 2    just verse 2
  python explain.py Ecc 1                       write notes for verses that have none
  python explain.py Ecc 1 --verses 2 9          redo verses 2 and 9
  python explain.py Ecc 1 --answers FILE        use prepared answers, no API call
  python explain.py Ecc 1 --check               re-check the notes after hand edits

The model call needs   pip install anthropic   and ANTHROPIC_API_KEY set.
Everything else runs on the standard library. Python 3.10 or newer.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))        # find build.py however this script is started
from build import CHAPTERS, chapter_id, find_book  # noqa: E402
GLOSSARY_FILE = HERE / "glossary.json"

MODEL = "claude-sonnet-5-5"
MAX_PATTERNS = 2     # per verse. More than this and the note turns into a list.
MAX_WORDS = 60

SYSTEM = """\
You write short notes for a Bible reader who knows no Hebrew.

You are given one verse of the Hebrew Bible as rows from a scholarly dataset,
and one or two patterns that a script found in those rows. Say what the reader
of the English cannot see.

Rules
- Explain only the listed patterns. If a pattern is not worth a reader's
  attention (for example the English differs only in tense or plural), leave
  it out. If nothing is left, return {"note": ""}.
- Use only facts in the sheet. Add no meanings, word histories, theology or
  anything else from memory.
- Never type Hebrew. To show a Hebrew word, write its row label in braces,
  like {r3}. The page puts the Hebrew there.
- Write numbers as digits, and only numbers that appear in the sheet.
- Use double quotes only around English that appears in the sheet.
- At most 2 sentences and 50 words. Plain words. Use a grammar term only if
  the sheet's glossary explains it.

Return JSON and nothing else: {"note": "..."}"""

POINTS = re.compile(r"[֑-ׇ]")            # vowel points and accents
ACCENTS = re.compile(r"[֑-ֽ֯]")      # accents only
HEBREW = re.compile(r"[֐-׿]")
FINAL_LETTERS = str.maketrans("ךםןףץ", "כמנפצ")


# ---- Small text helpers ------------------------------------------------------

def consonants(lemma: str) -> str:
    """Dictionary form without vowels: the bare consonants."""
    return POINTS.sub("", lemma).translate(FINAL_LETTERS)


def clean(hebrew: str) -> str:
    return ACCENTS.sub("", hebrew)


HELPERS = {"there", "let", "him", "it", "them", "us", "will", "shall", "may",
           "to", "the", "a", "of", "has", "have", "had", "he", "she", "they", "i"}
IRREGULAR = {"was": "be", "were": "be", "is": "be", "are": "be", "been": "be",
             "made": "make", "saw": "see", "seen": "see", "said": "say",
             "brought": "bring", "gave": "give", "given": "give", "did": "do",
             "done": "do", "went": "go", "gone": "go", "came": "come",
             "knew": "know", "known": "know"}


def stem(gloss: str) -> str:
    """Crude English stem, so tense and plural do not look like a new word.

    'there was' and 'be' both become 'be'. 'days' becomes 'day'. It is rough
    on purpose: the model gets the final say on what is worth mentioning.
    """
    out = []
    for word in re.findall(r"[a-z]+", gloss.lower()):
        if word in HELPERS:
            continue
        word = IRREGULAR.get(word, word)
        for suffix in ("ing", "ed", "es", "s", "er"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                break
        out.append(word.rstrip("e") or word)
    return " ".join(out)


def verse_list(numbers) -> str:
    numbers = sorted(set(numbers))
    return ("verse " if len(numbers) == 1 else "verses ") + ", ".join(map(str, numbers))


# ---- Step 1: find patterns (no model involved) -------------------------------

def pieces_of(verse: dict) -> list:
    """Every piece of the verse in reading order, tagged with its word number."""
    return [dict(piece, w=n) for n, word in enumerate(verse["words"], 1) for piece in word]


def find_patterns(verses: list) -> dict:
    """Return {verse number: [pattern, ...]}.

    Each pattern is reported once in the chapter, at the first verse that has
    room for it, so the reader is not told the same thing 30 times.

    To add a layer: add a detector here, and a line for it in make_sheet.
    """
    by_id = {p["id"]: p for v in verses for p in pieces_of(v)}

    # For every dictionary form: which English it gets, and where.
    english = {}
    for v in verses:
        for p in pieces_of(v):
            if p.get("count") and p.get("english"):
                groups = english.setdefault(p["strong"], {})
                group = groups.setdefault(stem(p["english"]), {"words": Counter(), "verses": []})
                group["words"][p["english"]] += 1
                group["verses"].append(v["verse"])

    used = set()
    found = {}
    for v in verses:
        rows = [p for p in pieces_of(v) if p.get("count")]   # meaning-carrying words
        candidates = []

        # A. Verb and subject disagree: singular verb, plural subject (or the reverse).
        for p in rows:
            subjects = p.get("subject", [])
            if p["pos"] == "verb" and p.get("person") and len(subjects) == 1:
                subject = by_id.get(subjects[0]["id"])
                if subject and {p.get("number"), subject.get("number")} == {"singular", "plural"}:
                    candidates.append({"type": "number_mismatch", "key": subject["strong"],
                                       "rows": [p["id"], subject["id"]]})

        # B. A noun leaning on its own plural: "vanity of vanities", "song of songs".
        for a, b in zip(rows, rows[1:]):
            if (a["strong"] == b["strong"] and a.get("state") == "construct"
                    and a.get("number") == "singular" and b.get("number") == "plural"):
                candidates.append({"type": "noun_of_its_plural", "key": a["strong"],
                                   "rows": [a["id"], b["id"]],
                                   "in_verse": sum(p["strong"] == a["strong"] for p in rows)})

        # C. Two different words with the same consonants, side by side in a verse.
        #    Usually a verb and a noun from one root: "let the earth sprout sprouts".
        #    Two-letter matches are skipped: too many are coincidence.
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                same = consonants(a["lemma"]) == consonants(b["lemma"])
                if a["strong"] != b["strong"] and same and len(consonants(a["lemma"])) >= 3:
                    candidates.append({"type": "same_consonants", "key": consonants(a["lemma"]),
                                       "rows": [a["id"], b["id"]]})

        # D. One verb used twice in different forms, or three times or more.
        verbs = {}
        for p in rows:
            if p["pos"] == "verb":
                verbs.setdefault(p["strong"], []).append(p)
        for strong, uses in verbs.items():
            forms = {p.get("type") for p in uses}
            if len(uses) >= 3 or (len(uses) == 2 and len(forms) == 2):
                candidates.append({"type": "verb_repeated", "key": strong,
                                   "rows": [p["id"] for p in uses]})

        # E. One Hebrew word, several English words across the chapter.
        for p in rows:
            groups = english.get(p["strong"], {})
            if len(groups) > 1 and p.get("english") and stem(p["english"]) != next(iter(groups)):
                candidates.append({
                    "type": "english_varies", "key": p["strong"], "rows": [p["id"]],
                    "variants": [(g["words"].most_common(1)[0][0], sorted(set(g["verses"])))
                                 for g in groups.values()],
                })

        for c in candidates:
            tag = (c["type"], c["key"])
            if tag in used or len(found.get(v["verse"], [])) >= MAX_PATTERNS:
                continue
            used.add(tag)
            found.setdefault(v["verse"], []).append(c)
    return found


# ---- Step 2: the fact sheet the model sees -----------------------------------

def grammar(p: dict) -> str:
    """The row's grammar as the dataset gives it, lightly spelled out."""
    if not p.get("count"):
        return p.get("type") or p["pos"]
    bits = [p["pos"]]
    if p["pos"] == "verb":
        if p.get("type"):
            bits.append("form: " + p["type"])
        if p.get("stem"):
            bits.append("pattern: " + p["stem"])
    shape = ", ".join(filter(None, [
        p.get("person") and p["person"] + " person", p.get("gender"), p.get("number")]))
    if shape:
        bits.append(shape)
    if p.get("state") == "construct":
        bits.append("construct")
    return "; ".join(bits)


def make_sheet(title: str, verse: dict, patterns: list, glossary: dict):
    """Return (sheet text, {row label: piece id}). title is like 'Genesis 1'."""
    rows = pieces_of(verse)
    label = {p["id"]: f"r{n}" for n, p in enumerate(rows, 1)}
    by_id = {p["id"]: p for p in rows}

    lines = [
        f"{title}:{verse['verse']}",
        f"English (Berean Standard Bible): {verse['english']}",
        "",
        "ROWS, in Hebrew reading order. Pieces of one written word share a w number.",
    ]
    for p in rows:
        line = (f"[{label[p['id']]}] w{p['w']} | {clean(p.get('he', '')) or '(not written)'} | "
                f"\"{p.get('english') or '(no English)'}\" | {grammar(p)}")
        if p.get("count"):
            line += f" | dictionary form {p['lemma']}, {p['count']} in chapter"
        lines.append(line)

    lines += ["", "PATTERNS FOUND BY THE SCRIPT"]
    terms = set()
    for n, pat in enumerate(patterns, 1):
        ids = pat["rows"]
        first = by_id[ids[0]]
        for i in ids:
            terms.update(filter(None, [by_id[i].get("type"), by_id[i].get("stem")]))
        if pat["type"] == "number_mismatch":
            verb, subject = by_id[ids[0]], by_id[ids[1]]
            text = (f"Verb and subject differ in number. The verb {label[verb['id']]} is "
                    f"{verb['number']}. Its subject {label[subject['id']]} is {subject['number']} in form.")
        elif pat["type"] == "noun_of_its_plural":
            terms.add("noun of its plural")
            text = (f"A noun leans on its own plural: {label[ids[0]]} (singular, construct) "
                    f"then {label[ids[1]]} (plural). This dictionary form ({first['lemma']}) "
                    f"appears {pat['in_verse']} times in this verse.")
        elif pat["type"] == "same_consonants":
            a, b = by_id[ids[0]], by_id[ids[1]]
            text = (f"Two different words built on the same consonants stand in this verse: "
                    f"{label[a['id']]} ({a['pos']}) and {label[b['id']]} ({b['pos']}). "
                    f"Shared consonants: {consonants(a['lemma'])}.")
        elif pat["type"] == "verb_repeated":
            uses = ", ".join(f"{label[i]} (form: {by_id[i].get('type')})" for i in ids)
            text = f"One verb appears {len(ids)} times in this verse: {uses}. Dictionary form {first['lemma']}."
        else:
            spread = "; ".join(f"\"{word}\" in {verse_list(vs)}" for word, vs in pat["variants"])
            text = (f"One dictionary form, more than one English word in the dataset's "
                    f"word-by-word English. {label[first['id']]} is \"{first['english']}\" here. "
                    f"Across the chapter the same dictionary form ({first['lemma']}) is "
                    f"rendered: {spread}.")
        lines.append(f"{n}. {text}")

    notes = {**glossary["forms"], **glossary["patterns"], **glossary.get("constructions", {})}
    explained = [t for t in sorted(terms) if t in notes]
    if explained:
        lines += ["", "GLOSSARY (rules of thumb you may use)"]
        lines += [f"- {t}: {notes[t][0]}. {notes[t][1]}" for t in explained]

    return "\n".join(lines), {v: k for k, v in label.items()}


# ---- Step 3: ask the model ----------------------------------------------------

def ask_model(sheet: str, complaint: str = "") -> str:
    """Send one fact sheet, get the model's raw answer back.

    This is the only function that knows which provider you use. To switch,
    rewrite these few lines and nothing else.
    """
    from anthropic import Anthropic      # imported here so the rest runs without it

    client = Anthropic()                 # reads ANTHROPIC_API_KEY
    if complaint:
        sheet += "\n\nYour last answer was rejected:\n" + complaint + "\nTry again."
    message = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=SYSTEM,
        messages=[{"role": "user", "content": sheet}],
    )
    return "".join(block.text for block in message.content if block.type == "text")


def read_answer(raw: str) -> str:
    """Pull the note out of the model's JSON answer."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("no JSON object in the answer")
    return str(json.loads(match.group(0)).get("note", "")).strip()


# ---- Step 4: check the answer -------------------------------------------------

def flatten(text: str) -> str:
    text = text.lower().replace("“", '"').replace("”", '"').replace("’", "'")
    return re.sub(r"\s+", " ", text)


def check(note: str, sheet: str, row_ids: set) -> list:
    """Return a list of problems. An empty list means the note passes.

    In a stored note a pointer looks like {o010010030031}: the id of a row.
    """
    problems = []
    pointers = re.findall(r"\{([^{}]*)\}", note)
    prose = re.sub(r"\{[^{}]*\}", " WORD ", note)

    if HEBREW.search(prose):
        problems.append("types Hebrew instead of pointing at a row")
    if not pointers:
        problems.append("points at no row")
    for pointer in pointers:
        if pointer not in row_ids:
            problems.append(f"points at {pointer}, which is not a row of this verse")

    allowed = set(re.findall(r"\b\d+\b", sheet))
    for number in re.findall(r"\b\d+\b", prose):
        if number not in allowed:
            problems.append(f"the number {number} is not in the sheet")

    haystack = flatten(sheet)
    for quote in re.findall(r'"([^"]+)"', flatten(prose)):
        if quote.strip(" .,;:!?…") not in haystack:
            problems.append(f'quotes "{quote}", which is not in the sheet')

    if len(prose.split()) > MAX_WORDS:
        problems.append(f"longer than {MAX_WORDS} words")
    return problems


# ---- Step 5: keep the results -------------------------------------------------

def load_notes(path: Path) -> dict:
    if not path.exists():
        return {}
    return {n["verse"]: n for n in json.loads(path.read_text(encoding="utf-8"))}


def save_notes(path: Path, notes: dict) -> None:
    lines = ",\n".join(json.dumps(notes[v], ensure_ascii=False) for v in sorted(notes))
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Write and check model notes for one chapter.")
    parser.add_argument("book", help='book name or abbreviation, e.g. Ecclesiastes, Ecc, "1 Samuel"')
    parser.add_argument("chapter", type=int)
    parser.add_argument("--verses", nargs="+", type=int, default=[], help="only these verses")
    parser.add_argument("--sheet", action="store_true", help="print fact sheets and stop")
    parser.add_argument("--check", action="store_true", help="re-check the saved notes and stop")
    parser.add_argument("--answers", type=Path, help="JSON file of prepared answers")
    args = parser.parse_args()

    book = find_book(args.book)
    title = f"{book['name']} {args.chapter}"
    cid = chapter_id(book, args.chapter)
    data_file = CHAPTERS / f"{cid}.json"
    notes_file = CHAPTERS / f"{cid}.notes.json"
    if not data_file.exists():
        raise SystemExit(f"No data for {title} yet. Run: python build.py {book['abbr']} {args.chapter}")

    verses = {v["verse"]: v for v in json.loads(data_file.read_text(encoding="utf-8"))}
    glossary = json.loads(GLOSSARY_FILE.read_text(encoding="utf-8"))
    patterns = find_patterns(list(verses.values()))
    notes = load_notes(notes_file)

    if args.sheet:
        for v in args.verses or sorted(patterns):
            if v in patterns:
                print(make_sheet(title, verses[v], patterns[v], glossary)[0],
                      end="\n\n" + "=" * 60 + "\n\n")
            else:
                print(f"{title}:{v}: no pattern found, so there is no sheet.\n")
        return

    if args.check:
        for v, record in sorted(notes.items()):
            if not record["note"]:
                continue
            sheet, row_ids = make_sheet(title, verses[v], patterns.get(v, []), glossary)
            problems = check(record["note"], sheet, set(row_ids.values()))
            if problems:
                record.update(status="rejected", problems=problems)
            else:
                record.pop("problems", None)
                if record["status"] == "rejected":
                    record["status"] = "unreviewed"
            print(f"verse {v:>2}  {record['status']:<10}  {'; '.join(problems)}")
        save_notes(notes_file, notes)
        return

    prepared = None
    if args.answers:
        prepared = json.loads(args.answers.read_text(encoding="utf-8"))
    wanted = args.verses or (
        sorted(int(k) for k in prepared if k.isdigit()) if prepared
        else [v for v in sorted(patterns) if v not in notes]
    )

    for v in wanted:
        if v not in patterns:
            print(f"verse {v:>2}  no pattern found, nothing to explain")
            continue
        sheet, label_to_id = make_sheet(title, verses[v], patterns[v], glossary)
        record = {"verse": v, "note": "", "status": "nothing",
                  "patterns": [p["type"] for p in patterns[v]],
                  "model": prepared.get("model", "prepared answers") if prepared else MODEL}
        problems = []
        for attempt in range(1 if prepared else 2):     # one retry for the model
            try:
                raw = (json.dumps(prepared[str(v)]) if prepared
                       else ask_model(sheet, "\n".join(problems)))
                note = read_answer(raw)
            except (KeyError, ValueError) as error:
                problems = [f"unreadable answer: {error}"]
                continue
            # The model wrote {r3}. Store the row's id instead, so the note
            # still points at the right word if the rows are ever renumbered.
            note = re.sub(r"\{(r\d+)\}", lambda m: "{" + label_to_id.get(m.group(1), m.group(1)) + "}", note)
            problems = check(note, sheet, set(label_to_id.values())) if note else []
            record["note"] = note
            if not problems:
                break
        if problems:
            record.update(status="rejected", problems=problems)
        elif record["note"]:
            record["status"] = "unreviewed"
        notes[v] = record
        print(f"verse {v:>2}  {record['status']:<10}  {'; '.join(problems)}")

    save_notes(notes_file, notes)
    print(f"\nwrote chapters/{notes_file.name}")


if __name__ == "__main__":
    main()
