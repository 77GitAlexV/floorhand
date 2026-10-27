"""What a worker said -> what they mean. Rules only, no model, English and Russian.

Used for voice transcripts (Muse Voice Transcribe or the browser's own speech
recognition) and for anything typed in the same box. The result is never applied
directly: marking.py turns it into a proposal the worker confirms.

    "two hundred fourteen done"    -> mark order 214
    "214 готов", "214-2 готов"     -> mark order 214 (piece 2)
    "двести четырнадцатый готов"   -> mark order 214
    "сделал три пуфа"              -> mark 3 pieces of kind "pouf"
    "кончилась ткань беж"          -> material is out: "ткань беж"
    "отмени последнюю", "undo"     -> undo my last mark
    "шил мешки 40 минут"           -> other work, 40 minutes
"""
import re
from dataclasses import dataclass, field

# --- number words ---------------------------------------------------------

EN_UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
            "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
            "seventeen": 17, "eighteen": 18, "nineteen": 19, "a": None}
EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
           "seventy": 70, "eighty": 80, "ninety": 90}
EN_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
          "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
          "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
          "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
          "thirtieth": 30, "fortieth": 40, "fiftieth": 50, "sixtieth": 60,
          "seventieth": 70, "eightieth": 80, "ninetieth": 90, "hundredth": 100}

_RU_ORD_END = ("ый", "ий", "ой", "ая", "ое", "ого", "ому", "ом", "ую", "ые", "ых", "ым", "ье",
               "ья", "ьего", "ьему", "ью", "ьей", "ьи", "ьих", "ьим")
_RU_CARD = {
    "ноль": 0, "один": 1, "одна": 1, "одно": 1, "одного": 1, "одну": 1, "два": 2, "две": 2,
    "двух": 2, "три": 3, "трех": 3, "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5,
    "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9,
    "девяти": 9, "десять": 10, "десяти": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
    "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20,
    "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70,
    "восемьдесят": 80, "девяносто": 90, "сто": 100, "двести": 200, "триста": 300,
    "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800,
    "девятьсот": 900, "тысяча": 1000, "тысячи": 1000, "тысяч": 1000,
}
_RU_ORD_STEMS = {
    "перв": 1, "втор": 2, "трет": 3, "четверт": 4, "пят": 5, "шест": 6, "седьм": 7,
    "восьм": 8, "девят": 9, "десят": 10, "одиннадцат": 11, "двенадцат": 12,
    "тринадцат": 13, "четырнадцат": 14, "пятнадцат": 15, "шестнадцат": 16,
    "семнадцат": 17, "восемнадцат": 18, "девятнадцат": 19, "двадцат": 20, "тридцат": 30,
    "сороков": 40, "пятидесят": 50, "шестидесят": 60, "семидесят": 70, "восьмидесят": 80,
    "девяност": 90, "сот": 100, "двухсот": 200, "трехсот": 300, "четырехсот": 400,
    "пятисот": 500, "шестисот": 600, "семисот": 700, "восьмисот": 800, "девятисот": 900,
    "тысячн": 1000,
}


def _ru_word(w: str) -> int | None:
    if w in _RU_CARD:
        return _RU_CARD[w]
    for stem, v in sorted(_RU_ORD_STEMS.items(), key=lambda kv: -len(kv[0])):
        if w.startswith(stem) and w[len(stem):] in _RU_ORD_END:
            return v
    return None


def word_value(w: str) -> int | None:
    """Value of one number word (any language), or None."""
    if w.isdigit():
        return int(w)
    if w in EN_UNITS and EN_UNITS[w] is not None:
        return EN_UNITS[w]
    if w in EN_TENS:
        return EN_TENS[w]
    if w in EN_ORD:
        return EN_ORD[w]
    if w in ("hundred", "thousand"):
        return {"hundred": 100, "thousand": 1000}[w]
    return _ru_word(w)


def _combine(values: list[int]) -> int:
    """Join spoken number parts into one number.

    "two hundred fourteen" -> 214, "двести четырнадцать" -> 214,
    "two fourteen" / "one oh five" -> 214 / 105 (people read sheet numbers in pairs).
    """
    total, cur, additive = 0, 0, True
    for v in values:
        if v in (100, 1000) and v != 0:
            cur = (cur or 1) * v
            if v == 1000:
                total, cur = total + cur, 0
            continue
        if cur and not _fits(cur, v):
            additive = False
            break
        cur += v
    if additive:
        return total + cur
    # Not a sum ("two fourteen"): read the parts as digits written one after another.
    return int("".join(str(v) for v in values))


def _fits(cur: int, v: int) -> bool:
    """Can v be added to cur as the next smaller part? (200 + 14 yes, 2 + 14 no)."""
    if cur >= 100 and cur % 100 == 0:
        return v < 100
    if cur >= 20 and cur % 10 == 0:
        return v < 10
    return False


# --- vocabulary -------------------------------------------------------------

DONE_WORDS = {"done", "ready", "finished", "finish", "completed", "complete", "made", "did",
              "built", "sewed", "sewn", "upholstered", "packed", "cut", "assembled",
              "готов", "готова", "готово", "готовы", "сделал", "сделала", "сделали",
              "сделано", "закончил", "закончила", "обил", "обила", "сшил", "сшила",
              "собрал", "собрала", "упаковал", "упаковала", "раскроил", "раскроила",
              "отшил", "отшила", "отметь", "отметить", "mark"}
NEGATIONS = {"not", "no", "isn't", "not yet", "не", "нет", "ещё", "еще"}
UNDO_WORDS = {"undo", "cancel", "отмени", "отменить", "отмена", "сними", "снять", "удали"}
LAST_WORDS = {"last", "последнюю", "последний", "последнее", "последняя", "previous"}
OUT_WORDS = ("ran out of", "out of", "run out of", "no more", "need more", "кончилась",
             "кончился", "кончилось", "кончились", "закончилась", "закончился",
             "закончилось", "закончились", "нет больше", "нужна", "нужен", "нужно",
             "не хватает", "мало")
OTHER_WORDS = ("other work", "extra work", "другое", "другую работу", "доделывал",
               "подгон", "подгонял", "ремонт", "ремонтировал", "чинил", "мешки", "мешков",
               "bags", "fitting", "repair", "repaired", "cleaning", "уборка", "убирал")
SEQ_WORDS = {"slash", "dash", "piece", "pc", "number", "дробь", "слэш", "тире", "штука",
             "штуку", "номер"}
ORDER_WORDS = {"order", "number", "no", "заказ", "заказа", "номер", "лист", "листок",
               "sheet"}
MINUTE_WORDS = ("minute", "minutes", "min", "mins", "минут", "минуты", "минуту", "мин")
HOUR_WORDS = ("hour", "hours", "hr", "hrs", "час", "часа", "часов")

# What people call a product kind -> products.kind in the catalog.
KIND_WORDS = {
    "pouf": "pouf", "poufs": "pouf", "пуф": "pouf", "пуфа": "pouf", "пуфы": "pouf",
    "пуфов": "pouf", "пуфик": "pouf", "пуфика": "pouf", "пуфиков": "pouf",
    "footstool": "pouf", "footstools": "pouf",
    "armchair": "armchair", "armchairs": "armchair", "кресло": "armchair",
    "кресла": "armchair", "кресел": "armchair",
    "chair": "chair", "chairs": "chair", "стул": "chair", "стула": "chair",
    "стулья": "chair", "стульев": "chair",
    "sofa": "sofa", "sofas": "sofa", "диван": "sofa", "дивана": "sofa", "диваны": "sofa",
    "диванов": "sofa", "rocker": "rocking chair", "rockers": "rocking chair",
    "качалка": "rocking chair", "качалки": "rocking chair", "качалок": "rocking chair",
    "daybed": "daybed", "daybeds": "daybed", "кушетка": "daybed", "кушетки": "daybed",
    "кушеток": "daybed",
}


@dataclass
class Intent:
    kind: str                       # mark | mark_qty | undo_last | material_out | other_work
    quote: str                      # the words this came from, as heard
    order: int | None = None
    seq: int | None = None
    qty: int | None = None
    product_kind: str | None = None
    product_word: str | None = None
    text: str = ""                  # material or other-work description
    minutes: int | None = None


@dataclass
class Parsed:
    transcript: str
    intents: list[Intent] = field(default_factory=list)
    negated: bool = False
    note: str = ""


def norm(text: str) -> str:
    t = text.lower().replace("ё", "е").replace("№", "#")
    t = re.sub(r"[,.!?;:«»\"()]", " ", t)
    t = re.sub(r"(?<=\w)-(?=[a-zа-я])", " ", t)   # "twenty-one" -> "twenty one"
    return re.sub(r"\s+", " ", t).strip()


def _tokens(t: str) -> list[str]:
    return re.findall(r"#?\d+(?:\s*[-/]\s*\d+)?|[a-zа-я']+", t)


def _number_runs(toks: list[str]) -> list[tuple[int, int, int, int | None]]:
    """[(start, end, value, seq)] runs of number words / digits in the token list."""
    runs, i = [], 0
    while i < len(toks):
        tok = toks[i]
        m = re.fullmatch(r"#?(\d+)(?:\s*[-/]\s*(\d+))?", tok)
        if m:
            runs.append((i, i + 1, int(m[1]), int(m[2]) if m[2] else None))
            i += 1
            continue
        if word_value(tok) is not None and tok not in ("a",):
            j, vals = i, []
            while j < len(toks) and (v := word_value(toks[j])) is not None \
                    and not re.fullmatch(r"#?\d.*", toks[j]):
                vals.append(v)
                j += 1
            runs.append((i, j, _combine(vals), None))
            i = j
            continue
        i += 1
    # "214 slash 2", "двести четырнадцать дробь два"
    merged = []
    k = 0
    while k < len(runs):
        s, e, v, seq = runs[k]
        if seq is None and k + 1 < len(runs) and runs[k + 1][0] == e + 1 \
                and toks[e] in SEQ_WORDS and runs[k + 1][2] < 100:
            merged.append((s, runs[k + 1][1], v, runs[k + 1][2]))
            k += 2
            continue
        merged.append((s, e, v, seq))
        k += 1
    return merged


def _minutes(t: str) -> int | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(HOUR_WORDS + MINUTE_WORDS) + r")\b", t)
    if m:
        n = float(m[1].replace(",", "."))
        return round(n * 60) if m[2] in HOUR_WORDS else round(n)
    toks = _tokens(t)
    for s, e, v, _ in _number_runs(toks):
        if e < len(toks) and toks[e] in HOUR_WORDS:
            return v * 60
        if e < len(toks) and toks[e] in MINUTE_WORDS:
            return v
    if re.search(r"\b(half an hour|полчаса)\b", t):
        return 30
    if re.search(r"\b(an hour|hour|час)\b", t):
        return 60
    return None


def parse(transcript: str, kinds: dict[str, str] | None = None) -> Parsed:
    """Find what the worker means. `kinds` adds catalog words (alias -> products.kind)."""
    t = norm(transcript)
    out = Parsed(transcript=transcript)
    if not t:
        out.note = "empty"
        return out
    words = set(t.split())
    vocab = {**KIND_WORDS, **(kinds or {})}

    # material is out: everything after the trigger words is the material
    for trig in OUT_WORDS:
        m = re.search(r"(?:^|\s)" + re.escape(trig) + r"\s+(.+)$", t)
        if m:
            what = re.sub(r"^(the|of|у нас|нас)\s+", "", m[1]).strip()
            out.intents.append(Intent("material_out", quote=m[0].strip(), text=what))
            return out

    if words & UNDO_WORDS:
        out.intents.append(Intent("undo_last", quote=t))
        return out

    toks = _tokens(t)
    if any(w in OTHER_WORDS for w in toks) or any(p in t for p in OTHER_WORDS if " " in p):
        mins = _minutes(t)
        desc = re.sub(r"\b\d+(?:[.,]\d+)?\s*(" + "|".join(HOUR_WORDS + MINUTE_WORDS) + r")\b",
                      "", t)
        desc = re.sub(r"\b(i|я|did|сделал|сделала|other work|другое)\b", " ", desc)
        out.intents.append(Intent("other_work", quote=t, text=re.sub(r"\s+", " ", desc).strip(),
                                  minutes=mins))
        return out

    neg = bool(re.search(r"\b(not|isn't|не|еще не|ещё не)\b\s*(yet\s+)?\b(done|ready|"
                         r"finished|готов\w*|сделал\w*|закончил\w*)", t))
    out.negated = neg
    if neg:
        out.note = "negated"
        return out

    for s, e, v, seq in _number_runs(toks):
        after = toks[e] if e < len(toks) else ""
        after2 = toks[e + 1] if e + 1 < len(toks) else ""
        kind = vocab.get(after) or (vocab.get(after2) if after2 and after not in DONE_WORDS
                                    else None)
        quote = " ".join(toks[s:e + (1 if kind and vocab.get(after) else 0)])
        if kind and v <= 50 and seq is None:
            out.intents.append(Intent("mark_qty", quote=quote, qty=v, product_kind=kind,
                                      product_word=after if vocab.get(after) else after2))
        elif v >= 1:
            before = toks[s - 1] if s > 0 else ""
            if v < 10 and seq is None and before not in ORDER_WORDS and not toks[s].startswith("#"):
                # "сделал два" with no kind word: a count of what? Leave it to the person.
                continue
            out.intents.append(Intent("mark", quote=quote, order=v, seq=seq))
    if not out.intents:
        # a kind word with no count: "сделал пуф" = one piece
        for w in toks:
            if w in vocab and words & DONE_WORDS:
                out.intents.append(Intent("mark_qty", quote=w, qty=1, product_kind=vocab[w],
                                          product_word=w))
                break
    if not out.intents:
        out.note = "no_number"
    return out
