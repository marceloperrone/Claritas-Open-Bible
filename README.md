# Claritas Open Bible

A small page that shows a chapter in Hebrew with the English beside it. Tap a
Hebrew word to see its dictionary form, its grammar in plain English, and every
other place it appears in the chapter. Some verses carry a short model-written
note on what the English hides.

Built so far: Genesis 1 and Ecclesiastes 1.

## The live site

A push to `main` redeploys the site on Railway. To put a new chapter online:

```
python build.py Jonah 1
git add chapters chapters.json
git commit -m "Add Jonah 1"
git push
```

The site is served by Caddy from the `Dockerfile` and `Caddyfile` here. Only
`index.html`, `glossary.json`, `chapters.json` and the `chapters/` folder go
into the image. The scripts are not served.

## Run it

```
python -m http.server            # then open http://localhost:8000
```

The two chapters are already built. To add another:

```
python build.py Jonah 1          # downloads the sources, writes chapters/jonah-1.json
python explain.py Jonah 1 --sheet    # see what patterns it found
```

Book names can be full (`Ecclesiastes`), short (`Ecc`) or quoted (`"1 Samuel"`).
No packages to install for these. Python 3.10 or newer.

## Files

| File | What it is |
| --- | --- |
| `build.py` | Reads the open datasets, writes one chapter's data file. |
| `explain.py` | Finds patterns, asks a model to explain them, checks the answers. |
| `index.html` | The reader. Plain HTML, CSS and JavaScript, no framework. |
| `glossary.json` | Plain-English wording for grammar terms. The page and `explain.py` both read it. |
| `chapters.json` | The list of built chapters. `build.py` keeps it up to date. |
| `chapters/<id>.json` | One chapter: one verse per line, every word piece with its fields. |
| `chapters/<id>.notes.json` | That chapter's notes, each with a status. |
| `chapters/<id>.answers.json` | The answers behind the current notes. See "Where the current notes came from". |
| `Dockerfile`, `Caddyfile` | How the live site is served. |

The page opens on the chapter named in the link (`#genesis-1`). Without one, it
opens on the chapter built most recently.

## The rule this is built on

Code finds the facts. The model only explains them.

1. `explain.py` looks for patterns in the rows with ordinary code.
2. For a verse with a pattern, it writes a fact sheet: that verse's rows and
   the pattern. `python explain.py Gen 1 --sheet --verses 3` shows one.
3. The model writes one or two sentences from the sheet. It may not type
   Hebrew. It points at a row instead, and the page shows that row's Hebrew.
4. The script rejects a note that types Hebrew, uses a number that is not in
   the sheet, or quotes English that is not in the sheet.
5. A note that passes is saved as `unreviewed`. Read it, and if you agree,
   change its status to `reviewed` in the notes file.

The checks catch invented words, numbers and quotes. They do not catch bad
reasoning from true facts. Step 5 is yours.

## The five patterns it looks for

- A verb and its subject that differ in number (singular with plural).
- A noun leaning on its own plural, like "vanity of vanities".
- Two different words on the same consonants in one verse, usually a verb and
  a noun from one root. This one can be a coincidence, so read those notes
  with extra care.
- One verb twice in a verse in different forms, or three times or more.
- One Hebrew word that gets several English words across the chapter.

Each pattern is reported once per chapter, at the first verse with room for it,
and a verse gets at most two. A verse with no pattern gets no note. The model
may also decide a pattern is not worth a note and return nothing.

To add a layer, write a new detector in `find_patterns` and a line for it in
`make_sheet`.

## Using a model through the API

```
pip install anthropic
export ANTHROPIC_API_KEY=...          # your key
python explain.py Ecc 1 --verses 2    # redo verse 2
python explain.py Jonah 1             # every verse that has no note yet
python explain.py Ecc 1 --check       # re-check the notes after hand edits
```

The model name is the `MODEL` line at the top of `explain.py`. To use another
provider, rewrite the `ask_model` function. Nothing else knows about it.

## Where the current notes came from

No API key was available when this starter was made. The notes for both
chapters were written by Claude in a chat session, from the same fact sheets
and under the same rules, then run through the same checks with:

```
python explain.py Gen 1 --answers chapters/genesis-1.answers.json
python explain.py Ecc 1 --answers chapters/ecclesiastes-1.answers.json
```

You can use that route too: print a sheet with `--sheet`, paste it into any
chat model along with the rules at the top of `explain.py`, and put the answers
in a file shaped like those.

## Known limits

- Hebrew and English Bibles number some verses differently (many psalms, for
  one). `build.py` pairs verses by number and prints a warning when the verse
  counts differ. It does not fix the pairing.
- The 39 books of the Hebrew Bible only. The dataset used here has no New
  Testament.
- The word-by-word English comes from the dataset and is not the Berean text,
  so the two can differ ("vanity" beside "futility").

## Checks you can repeat

`build.py` prints the repeated words. These match figures that are easy to
confirm in a commentary:

| Chapter | Word | Count |
| --- | --- | --- |
| Genesis 1 | God (elohim) | 32 |
| Genesis 1 | good (tov) | 7 |
| Genesis 1 | created (bara) | 5 |
| Ecclesiastes 1 | vanity (hevel) | 6, five of them in verse 2 |
| Ecclesiastes 1 | sun (shemesh) | 5 |

## Sources and licences

- MACULA Hebrew Linguistic Datasets, available at
  https://github.com/Clear-Bible/macula-hebrew/ (CC BY 4.0). Attribution is
  required if you publish anything built on it.
- Berean Standard Bible (public domain), via
  https://github.com/scrollmapper/bible_databases

The wording in `glossary.json` and the grammar hints in `index.html` are
beginner rules of thumb written for this starter. They are not from the
datasets.
