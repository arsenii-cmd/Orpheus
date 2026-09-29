"""Text embeddings for searching the memory by meaning ("пароль от интернета" finds "ключ сети").

paraphrase-multilingual-MiniLM-L12-v2 through fastembed (onnxruntime, CPU): 384 numbers per phrase,
a few milliseconds each. Not through Ollama: it has one model slot, and embeddings would push the
language model off the GPU.

Chosen on a Russian test of 12 questions against 12 notes and past conversations: right answer first
12 of 12, and — what matters for deciding whether a find is worth bringing up at all — the best
match stands out from the rest by 0.21-0.71 for real questions and 0.08-0.26 for "привет", "спасибо"
and the like (multilingual-e5-small: 11/12, and the two ranges overlap).
"""

import threading

import numpy as np

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Everyday sentences about nothing in particular. How much a find beats their mean similarity to the
# question says whether it is really related — the same yardstick with 1 record or 10 000.
ANCHORS = """Сегодня хорошая погода. Я пошёл в магазин за хлебом. Кошка спит на подоконнике.
Поезд прибывает в восемь утра. В парке много людей. Он читает книгу вечером. Мы смотрели фильм в кино.
Нужно помыть посуду. Вода кипит при ста градусах. Завтра будет понедельник. Музыка играет громко.
Машина стоит во дворе. Дети играют в футбол. Она пьёт чай с лимоном. Компьютер медленно работает.
Зимой бывает холодно. Я люблю гулять по вечерам. На столе лежит ручка. Телефон разрядился.
Птицы улетают на юг.""".replace("\n", " ").split(". ")


class Embedder:
    def __init__(self, cache_dir=None, model=MODEL):
        self.model_name = model
        self.cache_dir = str(cache_dir) if cache_dir else None
        self._model = None
        self._lock = threading.Lock()
        self._recent = {}  # one phrase is embedded for several searches in a row: the memory, "Личное", notes

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.model_name, cache_dir=self.cache_dir)
        return self._model

    def anchors(self):
        if getattr(self, "_anchors", None) is None:
            self._anchors = self.embed(ANCHORS)
        return self._anchors

    def embed(self, texts):
        """list of str -> float32 array (n, dim), each row of length 1."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        texts = list(texts)
        if len(texts) == 1 and texts[0] in self._recent:
            return self._recent[texts[0]].copy()
        with self._lock:
            vecs = np.array(list(self._load().embed(texts)), dtype=np.float32)
        vecs = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)
        if len(texts) == 1:
            if len(self._recent) >= 16:
                self._recent.pop(next(iter(self._recent)))
            self._recent[texts[0]] = vecs.copy()
        return vecs


def load_embedder(models_dir, log=print):
    """The model for the search by meaning (downloaded into models_dir/fastembed the first time);
    None when it cannot be had — the memory is then searched by words only."""
    try:
        embedder = Embedder(cache_dir=models_dir / "fastembed")
        embedder.embed(["проверка"])
        return embedder
    except Exception as exc:
        log("поиск по смыслу выключен: %s" % exc)
        return None
