"""Language names, language detection and romanization (no external models).

Detection works in layers:

1. **Script**: Cyrillic, Greek, Arabic, Hebrew, Devanagari, Han, kana, Hangul,
   Thai and others narrow the language directly, with letter checks inside
   a script (Ukrainian і/ї/є, Persian پ/چ/گ, Japanese kana and so on).
2. **Latin-script evidence**: common short words and distinctive letters
   (ñ, ß, ø, ł, ş, ő, ă, đ …) for about twenty languages.
3. **Your data**: letter-trigram profiles built from your translation
   memory, so languages you import become detectable too.

Short texts are hard; results include a confidence so callers can ask.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter

LANGUAGES = {
    "af": "Afrikaans", "ar": "Arabic", "bg": "Bulgarian", "bn": "Bengali", "ca": "Catalan", "cs": "Czech",
    "cy": "Welsh", "da": "Danish", "de": "German", "el": "Greek", "en": "English", "eo": "Esperanto",
    "es": "Spanish", "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish", "fr": "French",
    "ga": "Irish", "gl": "Galician", "gu": "Gujarati", "he": "Hebrew", "hi": "Hindi", "hr": "Croatian",
    "hu": "Hungarian", "hy": "Armenian", "id": "Indonesian", "is": "Icelandic", "it": "Italian", "ja": "Japanese",
    "ka": "Georgian", "km": "Khmer", "kn": "Kannada", "ko": "Korean", "la": "Latin", "lt": "Lithuanian",
    "lv": "Latvian", "mk": "Macedonian", "ml": "Malayalam", "mr": "Marathi", "ms": "Malay", "nb": "Norwegian",
    "ne": "Nepali", "nl": "Dutch", "pa": "Punjabi", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian",
    "ru": "Russian", "sk": "Slovak", "sl": "Slovenian", "sq": "Albanian", "sr": "Serbian", "sv": "Swedish",
    "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "th": "Thai", "tl": "Tagalog", "tr": "Turkish",
    "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese", "yi": "Yiddish", "zh": "Chinese", "zu": "Zulu",
    "am": "Amharic", "my": "Burmese", "lo": "Lao", "si": "Sinhala", "mn": "Mongolian", "kk": "Kazakh",
    "be": "Belarusian", "az": "Azerbaijani", "uz": "Uzbek", "ha": "Hausa", "yo": "Yoruba", "ig": "Igbo",
}
ALIASES = {
    "español": "es", "castilian": "es", "castellano": "es", "français": "fr", "deutsch": "de", "italiano": "it",
    "português": "pt", "brazilian": "pt", "nederlands": "nl", "flemish": "nl", "svenska": "sv", "dansk": "da",
    "norsk": "nb", "bokmål": "nb", "bokmal": "nb", "suomi": "fi", "polski": "pl", "čeština": "cs", "türkçe": "tr",
    "русский": "ru", "українська": "uk", "ελληνικά": "el", "中文": "zh", "mandarin": "zh", "cantonese": "zh",
    "日本語": "ja", "한국어": "ko", "हिन्दी": "hi", "farsi": "fa", "filipino": "tl", "bahasa": "id",
    "chinese simplified": "zh", "chinese traditional": "zh", "magyar": "hu", "română": "ro",
}
_BY_NAME = {name.lower(): code for code, name in LANGUAGES.items()}


def name(code: str) -> str:
    return LANGUAGES.get(code, code)


def code_for(text: str) -> str | None:
    """'Spanish', 'es', 'español', 'es-MX' -> 'es'."""
    t = text.strip().lower().rstrip(".?!")
    if t in LANGUAGES:
        return t
    base = re.split(r"[-_]", t)[0]
    if base in LANGUAGES and len(base) in (2, 3):
        return base
    return _BY_NAME.get(t) or ALIASES.get(t)


# ------------------------------------------------------------------- scripts
_SCRIPTS = [  # (first, last, script)
    (0x0041, 0x024F, "Latin"), (0x1E00, 0x1EFF, "Latin"), (0x0370, 0x03FF, "Greek"), (0x1F00, 0x1FFF, "Greek"),
    (0x0400, 0x052F, "Cyrillic"), (0x0530, 0x058F, "Armenian"), (0x0590, 0x05FF, "Hebrew"),
    (0x0600, 0x06FF, "Arabic"), (0x0750, 0x077F, "Arabic"), (0xFB50, 0xFDFF, "Arabic"), (0xFE70, 0xFEFF, "Arabic"),
    (0x0900, 0x097F, "Devanagari"), (0x0980, 0x09FF, "Bengali"), (0x0A00, 0x0A7F, "Gurmukhi"),
    (0x0A80, 0x0AFF, "Gujarati"), (0x0B80, 0x0BFF, "Tamil"), (0x0C00, 0x0C7F, "Telugu"), (0x0C80, 0x0CFF, "Kannada"),
    (0x0D00, 0x0D7F, "Malayalam"), (0x0D80, 0x0DFF, "Sinhala"), (0x0E00, 0x0E7F, "Thai"), (0x0E80, 0x0EFF, "Lao"),
    (0x1000, 0x109F, "Myanmar"), (0x10A0, 0x10FF, "Georgian"), (0x1200, 0x137F, "Ethiopic"),
    (0x1780, 0x17FF, "Khmer"), (0x1100, 0x11FF, "Hangul"), (0x3130, 0x318F, "Hangul"), (0xAC00, 0xD7AF, "Hangul"),
    (0x3040, 0x309F, "Kana"), (0x30A0, 0x30FF, "Kana"), (0x4E00, 0x9FFF, "Han"), (0x3400, 0x4DBF, "Han"),
    (0xF900, 0xFAFF, "Han"),
]
SCRIPT_LANG = {"Greek": "el", "Armenian": "hy", "Bengali": "bn", "Gurmukhi": "pa", "Gujarati": "gu", "Tamil": "ta",
               "Telugu": "te", "Kannada": "kn", "Malayalam": "ml", "Sinhala": "si", "Thai": "th", "Lao": "lo",
               "Myanmar": "my", "Georgian": "ka", "Ethiopic": "am", "Khmer": "km", "Hangul": "ko"}


def script_of(ch: str) -> str | None:
    o = ord(ch)
    for lo, hi, script in _SCRIPTS:
        if lo <= o <= hi:
            return script if script != "Latin" or ch.isalpha() else None
    return None


def scripts(text: str) -> Counter:
    return Counter(s for s in map(script_of, text) if s)


# ------------------------------------------------------- Latin-script clues
STOPWORDS = {
    "en": "the and is are of to in that it you was for on with as have be this not but they at what i we he she my hello hi thanks yes please goodbye",
    "es": "el la los las de que y en un una es por con para no se lo como más pero sus le ya o este sí porque muy hola gracias adiós buenos buenas sí señor favor",
    "fr": "le la les de des et est un une que qui en dans pour pas ne sur au avec il elle je vous nous ce sont bonjour bonsoir merci oui salut au revoir monsieur madame",
    "de": "der die das und ist nicht ein eine zu den mit von sich auf ich es sie du wir für auch dem im wie hallo danke bitte tschüss guten ja nein",
    "it": "il la di che e è un una per non in sono con lo gli le si del della mi ho ma come anche questo ciao grazie prego buongiorno arrivederci sì",
    "pt": "o a os as de que e do da em um uma para é não com se por mais como mas foi ao seu sua você olá obrigado obrigada tchau bom dia sim",
    "nl": "de het een en is van dat in te niet op ik je zijn met voor er maar ook als wat hij zij hallo dank bedankt dag goedemorgen ja nee",
    "sv": "och att det som en är på för av med inte jag till den har var de ett om men vi så hej tack hejdå ja nej",
    "da": "og at det en er på for af med ikke jeg til den har var de et om men vi så som han hej tak farvel ja nej",
    "nb": "og det er en på for av med ikke jeg til den har var de et om men vi så som hun ikkje hei takk ha det ja nei",
    "fi": "ja on ei se että hän oli mutta kuin niin tämä ovat olla minä sinä me te he mitä kun eikä myös tai jos vain hei kiitos moi kyllä",
    "pl": "i w nie na się z jest że to do co jak ale o tak po jego czy mi już przez dla cześć dziękuję dzień dobry tak",
    "cs": "a v se na je že to s z do jsem není ale jak o by pro tak jsou mi už co ve ahoj děkuji dobrý den ano",
    "tr": "ve bir bu da de için ile ne çok ben sen o var değil mi gibi daha ama olarak merhaba teşekkürler evet hayır günaydın",
    "id": "yang dan di ini itu dengan untuk tidak dari dalam akan pada saya ada juga adalah kami kita halo terima kasih selamat",
    "ro": "și de la în a nu este că un o cu pe se care mai din pentru sunt ce eu bună mulțumesc da",
    "hu": "a az és hogy nem is egy van meg de el ez én te mi azt csak ki volt már szia köszönöm igen nem",
    "vi": "và của là có không một những được cho người này trong với đã tôi các anh em đang trên rất",
    "sw": "na ya wa kwa ni la katika za kuwa hii yake au cha si mimi wewe sisi habari asante jambo ndiyo",
    "tl": "ang ng sa na at mga ay ko ako ka siya hindi ito para kung may",
    "ca": "el la els les de que i en un una és per amb no es al del com però",
    "hr": "i je u se na da su za ne to od s sam kao ali što ja ti mi",
}
_STOP = {lang: set(words.split()) for lang, words in STOPWORDS.items()}
LETTER_HINTS = {
    "es": "ñ¿¡", "pt": "ãõç", "fr": "çœèêëàâùûîï", "de": "ßäöü", "sv": "åäö", "da": "æøå", "nb": "æøå",
    "fi": "äö", "pl": "łąęśźżćń", "cs": "řěůčšž", "sk": "ľĺŕäô", "tr": "şğıİ", "hu": "őű", "ro": "ășțâî",
    "vi": "đơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹàáâãèéêìíòóôõùúýă", "is": "þðæ", "lv": "āēīūģķļņ",
    "lt": "ąčęėįšųūž", "ca": "ŀç", "hr": "čćđšž", "et": "õäöü", "eo": "ĉĝĥĵŝŭ", "ga": "áéíóú",
}
SUFFIX_HINTS = {  # common word endings (inflection and derivation), a little evidence each
    "fi": ("ssa", "ssä", "lla", "llä", "sta", "stä", "ksi", "aan", "ään", "kaan", "kään"),
    "hu": ("ban", "ben", "nak", "nek", "ból", "ből", "ság", "ség", "ott", "ett"),
    "tr": ("lar", "ler", "dır", "dir", "yor", "mış", "miş", "sın", "sin"),
    "pl": ("ów", "ami", "ach", "ość", "ego", "ych"), "cs": ("ost", "ých", "ého", "ými"),
    "de": ("ung", "heit", "keit", "chen", "lich", "isch"), "nl": ("heid", "lijk", "tje", "ijk"),
    "es": ("ción", "mente", "ado", "ido", "aba"), "pt": ("ção", "ões", "mente", "ado", "inho"),
    "it": ("zione", "mente", "ato", "ito", "etto"), "fr": ("tion", "ment", "eux", "aient", "ée"),
    "sv": ("het", "arna", "orna", "ning"), "id": ("kan", "nya", "lah"), "ro": ("ului", "ilor", "ește"),
    "en": ("ing", "tion", "ness", "ly", "ed"),
}
_LETTER_OWNERS = Counter(ch for letters in LETTER_HINTS.values() for ch in set(letters))


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", text.lower())


def trigrams(text: str) -> Counter:
    t = " " + re.sub(r"[\W\d_]+", " ", text.lower()).strip() + " "
    return Counter(t[i : i + 3] for i in range(len(t) - 2))


def build_profile(texts, size: int = 300) -> list[str]:
    counts: Counter = Counter()
    for t in texts:
        counts.update(trigrams(t))
    return [g for g, _ in counts.most_common(size)]


def _profile_distance(text: str, profile: list[str]) -> float:
    """Out-of-place distance (Cavnar & Trenkle), scaled to 0 (same) .. 1 (unrelated)."""
    rank = {g: i for i, g in enumerate(profile)}
    doc = [g for g, _ in trigrams(text).most_common(150)]
    if not doc or not profile:
        return 1.0
    worst = len(profile)
    return sum(min(abs(rank[g] - i), worst) if g in rank else worst for i, g in enumerate(doc)) / (worst * len(doc))


def detect(text: str, profiles: dict[str, list[str]] | None = None) -> list[tuple[str, float]]:
    """Ranked ``(language code, confidence 0..1)`` guesses for ``text``."""
    sc = scripts(text)
    if not sc:
        return []
    total = sum(sc.values())
    main, n = sc.most_common(1)[0]
    if main == "Kana" or (main == "Han" and sc.get("Kana")):
        return [("ja", 0.95)]
    if main == "Han":
        return [("zh", 0.9), ("ja", 0.1)]
    if main in SCRIPT_LANG:
        return [(SCRIPT_LANG[main], 0.95)]
    if main == "Hebrew":
        return [("yi", 0.6), ("he", 0.4)] if re.search(r"[ײװױ]|יִ|אַ|אָ", text) else [("he", 0.95)]
    if main == "Arabic":
        if re.search(r"[ٹڈڑںےۓھ]", text):
            return [("ur", 0.9), ("fa", 0.1)]
        if re.search(r"[پچژگکی]", text):
            return [("fa", 0.85), ("ur", 0.15)]
        return [("ar", 0.9)]
    if main == "Devanagari":
        return [("hi", 0.7), ("mr", 0.2), ("ne", 0.1)]
    if main == "Cyrillic":
        if re.search(r"[іїєґ]", text, re.I):
            return [("uk", 0.95)]
        if re.search(r"[ђћџљњ]", text, re.I):
            return [("sr", 0.9), ("mk", 0.1)]
        if re.search(r"[ѓќѕ]", text, re.I):
            return [("mk", 0.9)]
        if re.search(r"[ўі]", text, re.I):
            return [("be", 0.9)]
        if re.search(r"[әғқңөұүһ]", text, re.I):
            return [("kk", 0.9)]
        if re.search(r"ъ", text, re.I) and not re.search(r"[ыэё]", text, re.I):
            return [("bg", 0.8), ("ru", 0.2)]
        return [("ru", 0.85), ("bg", 0.1), ("uk", 0.05)]
    if main == "Greek":
        return [("el", 0.95)]
    # Latin script: common words, distinctive letters and any profiles from your data.
    words = _words(text)
    scores: Counter = Counter()
    for lang, stop in _STOP.items():
        hits = sum(1 for w in words if w in stop)
        if hits:
            scores[lang] += hits / max(1, len(words)) * 3
    lower = text.lower()
    for lang, letters in LETTER_HINTS.items():
        # a letter only one language uses (đ, ő, ł) is strong evidence; shared ones (é, ä) are weak
        hits = sum(lower.count(ch) * (0.6 if _LETTER_OWNERS[ch] == 1 else 0.15) for ch in letters)
        if hits:
            scores[lang] += min(1.8, hits)
    for lang, endings in SUFFIX_HINTS.items():
        hits = sum(1 for w in words if len(w) > 4 and w.endswith(endings))
        if hits:
            scores[lang] += min(1.0, hits * 0.25)
    for lang, profile in (profiles or {}).items():
        scores[lang] += max(0.0, 1.0 - _profile_distance(text, profile)) * 2
    if not scores:
        return [("en", 0.2)] if n / total > 0.5 else []
    best = scores.most_common(6)
    total_score = sum(s for _, s in best)
    return [(lang, round(s / total_score, 3)) for lang, s in best]


# -------------------------------------------------------------- romanization
_CYRILLIC = dict(zip("абвгдезийклмнопрстуфыэ", "abvgdezijklmnoprstufye"))
_CYRILLIC.update({"ё": "yo", "ж": "zh", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "",
                  "ь": "", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "w",
                  "ђ": "đ", "ћ": "ć", "џ": "dž", "љ": "lj", "њ": "nj", "ј": "j"})
_GREEK = dict(zip("αβγδεζηικλμνξοπρστυφωάέήίόύώϊϋΐΰς", "avgdeziiklmnxoprstyfoaeiioyoiyiys"))
_GREEK.update({"θ": "th", "χ": "ch", "ψ": "ps"})


def romanize(text: str) -> str:
    """Latin letters for Cyrillic and Greek text; other scripts are left as they are."""
    out = []
    for ch in text:
        low = ch.lower()
        rep = _CYRILLIC.get(low, _GREEK.get(low))
        if rep is None:
            out.append(ch)
        else:
            out.append(rep.capitalize() if ch != low and rep else rep)
    return "".join(out)


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def save_profiles(path: str, profiles: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False)
