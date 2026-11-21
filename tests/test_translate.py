import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PHRASEBOOK = os.path.join(HERE, "..", "examples", "phrasebook_en_es.tsv")
DICTIONARY = os.path.join(HERE, "..", "examples", "dictionary_en_es.tsv")

SAMPLES = {
    "en": "The cat is on the table and it is sleeping.", "es": "Necesito una habitación para dos noches, por favor.",
    "fr": "Où est la bibliothèque? Je dois être à l'école très tôt.", "de": "Die Katze ist auf dem Tisch und sie schläft.",
    "it": "Il gatto è sul tavolo e dorme.", "pt": "O gato está na mesa e não quer descer.",
    "nl": "De kat ligt op de tafel en slaapt.", "sv": "Katten sover på bordet och vill inte gå ner.",
    "da": "Katten sover på bordet og vil ikke ned.", "fi": "Asun Helsingissä ja työskentelen toimistossa.",
    "pl": "Kot jest na stole i śpi.", "cs": "Kočka spí na stole a nechce slézt dolů.",
    "tr": "Kedi masanın üstünde uyuyor ve çok mutlu.", "hu": "A macska az asztalon alszik és nem jön le.",
    "ro": "Pisica doarme pe masă și nu vrea să coboare.", "vi": "Con mèo đang ngủ trên bàn.",
    "id": "Kucing itu tidur di atas meja dan tidak mau turun.", "ru": "Кошка спит на столе.",
    "uk": "Кішка спить на столі.", "el": "Η γάτα κοιμάται στο τραπέζι.", "zh": "猫在桌子上睡觉。",
    "ja": "猫がテーブルの上で寝ています。", "ko": "고양이가 탁자 위에서 자고 있다.", "ar": "القطة نائمة على الطاولة",
    "fa": "گربه روی میز خوابیده است", "hi": "बिल्ली मेज़ पर सो रही है।", "he": "החתול ישן על השולחן",
    "th": "แมวกำลังนอนอยู่บนโต๊ะ",
}


class LangTest(unittest.TestCase):
    def test_detect_many_languages(self):
        from libra.lang import detect

        wrong = {code: detect(text)[0][0] for code, text in SAMPLES.items() if detect(text)[0][0] != code}
        self.assertEqual(wrong, {})
        self.assertEqual(detect("Bonjour")[0][0], "fr")
        self.assertEqual(detect("12345 !!!"), [])

    def test_names_and_romanize(self):
        from libra.lang import LANGUAGES, code_for, romanize

        self.assertEqual([code_for(x) for x in ("Spanish", "es-MX", "español", "Japanese", "Klingon")],
                         ["es", "es", "es", "ja", None])
        self.assertFalse([v for v in LANGUAGES.values() if " " in v])
        self.assertEqual(romanize("Привет, мир!"), "Privet, mir!")
        self.assertEqual(romanize("Ελληνικά"), "Ellinika")


class TranslateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = self.tmp.name
        from libra.translate import Translator, read_pairs

        self.t = Translator()
        self.t.add_pairs(read_pairs(PHRASEBOOK, "en", "es"), "en", "es", "phrasebook")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def test_exact_both_directions(self):
        r = self.t.translate("where is the hotel", "es", "en")
        self.assertEqual((r.text, r.method, r.confidence), ("¿Dónde está el hotel?", "exact", 1.0))
        self.assertEqual(self.t.translate("¿Cómo estás?", "English").text, "How are you?")
        self.assertEqual(self.t.add_pairs([("Hello.", "Hola.")], "en", "es"), 0)  # duplicates ignored

    def test_alignment_learns_words_and_drops_one_sided_matches(self):
        n = self.t.align("en", "es")
        self.assertGreater(n, 20)
        self.assertEqual(self.t.lookup("cat", "en", "es")[0], "gato")
        self.assertEqual(self.t.lookup("dog", "en", "es")[0], "perro")
        self.assertEqual(self.t.lookup("perro", "es", "en")[0], "dog")  # reverse lookup
        self.assertNotIn("es", self.t.lookup("black", "en", "es"))  # symmetrization removed noise

    def test_adapt_gloss_unknown_and_none(self):
        self.t.align("en", "es")
        r = self.t.translate("I have a horse.", "es", "en")
        self.assertEqual((r.text, r.method), ("Tengo un [horse].", "adapted"))
        self.assertTrue(any("untranslated" in n for n in r.notes))
        self.t.add_dictionary(DICTIONARY, "en", "es")
        r = self.t.translate("I have a horse.", "es", "en")
        self.assertEqual(r.text, "Tengo un caballo.")
        self.assertTrue(any("agreement" in n for n in r.notes))  # Spanish has grammatical gender
        r = self.t.translate("My friend sleeps.", "es", "en")
        self.assertEqual((r.text, r.method), ("Mi amigo duerme.", "gloss"))
        self.assertLess(r.confidence, 0.5)
        r = self.t.translate("The quantum computer exploded.", "es", "en")
        self.assertEqual(r.method, "none")
        r = self.t.translate("Guten Morgen", "es", "de")
        self.assertEqual(r.method, "none")
        self.assertIn("libra translate add", r.notes[-1])

    def test_multi_sentence_and_pivot(self):
        r = self.t.translate("Good morning. Where is the station?", "es", "en")
        self.assertEqual(r.text, "Buenos días. ¿Dónde está la estación?")
        self.assertEqual(len(r.parts), 2)
        self.t.add_pairs([("Good morning.", "Bonjour."), ("Thank you.", "Merci.")], "en", "fr")
        r = self.t.translate("Gracias.", "fr", "es")  # es->fr has no data: goes through English
        self.assertEqual((r.text, r.method), ("Merci.", "pivot"))

    def test_formats(self):
        from libra.translate import read_pairs

        d = self.tmp.name
        paths = {}
        for name, body in {
            "a.tsv": "1\tHello.\t2\tHola.\n",
            "b.jsonl": json.dumps({"translation": {"en": "Yes.", "es": "Sí."}}) + "\n",
            "c.csv": "English,Spanish\nNo.,No.\n",
            "d.tmx": '<tmx><body><tu><tuv xml:lang="en-US"><seg>Thanks</seg></tuv>'
                     '<tuv xml:lang="es"><seg>Gracias</seg></tuv></tu></body></tmx>',
            "e.en": "one\ntwo\n", "e.es": "uno\ndos\n",
        }.items():
            paths[name] = os.path.join(d, name)
            with open(paths[name], "w") as f:
                f.write(body)
        self.assertEqual(list(read_pairs(paths["a.tsv"], "en", "es")), [("Hello.", "Hola.")])
        self.assertEqual(list(read_pairs(paths["b.jsonl"], "en", "es")), [("Yes.", "Sí.")])
        self.assertEqual(list(read_pairs(paths["c.csv"], "en", "es")), [("No.", "No.")])
        self.assertEqual(list(read_pairs(paths["d.tmx"], "en", "es")), [("Thanks", "Gracias")])
        self.assertEqual(list(read_pairs(paths["e.en"], "en", "es", paths["e.es"])), [("one", "uno"), ("two", "dos")])

    def test_neural_pipeline(self):
        from libra.train import TrainConfig, train

        out = os.path.join(self.tmp.name, "data")
        self.assertEqual(self.t.export_training(out, "en", "es", both=True), 158)
        ckpt = os.path.join(self.tmp.name, "model")
        train(TrainConfig(data=[out], out=ckpt, vocab_size=400, block_size=48, n_layer=1, n_head=2, n_embd=32,
                          batch_size=8, steps=6, eval_every=0, log_every=100, device="cpu"), log=lambda *a: None)
        self.t.set_model("en", "es", ckpt)
        r = self.t.translate("Is the submarine yellow?", "es", "en")
        self.assertEqual(r.method, "neural")  # tiny model: output is nonsense, but the path is wired up
        self.assertEqual(self.t.translate("Where is the hotel?", "es", "en").method, "exact")  # memory still first

    def test_skill_routing(self):
        from libra.skills import default_registry

        reg = default_registry(log=None, load_default_dir=False, catalog=None)
        call = reg.route("How do you say thank you very much in Spanish?")
        self.assertEqual(call, ("translate", {"text": "thank you very much", "to": "Spanish"}))
        res = reg.run(*call)
        self.assertTrue(res.text.startswith("Muchas gracias."))
        self.assertIsNone(reg.route("translate this to blue"))
        self.assertEqual(reg.route("what language is this: Hej tack")[0], "detect_language")


if __name__ == "__main__":
    unittest.main()
