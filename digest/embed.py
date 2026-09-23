"""Stage 2a — Embeddings.

An embedding turns a piece of text into a list of numbers (a "vector") that
encodes its meaning. Two articles about the same event land close together in
that number-space even when they share almost no words, which is exactly the
problem PRD section 7 describes. Keyword matching cannot do this.

This is an AI model, but NOT a language model: it generates no text and answers
no questions. It only converts text to coordinates.

Two providers are supported behind one interface:

  local   sentence-transformers, runs on this machine, free, no API key.
  voyage  Voyage AI's hosted API — Anthropic's documented recommendation,
          better quality, needs VOYAGE_API_KEY.

They are NOT interchangeable at the same threshold. Every embedding model has
its own similarity distribution, so switching provider means re-tuning
config.SIMILARITY_THRESHOLD against the labelled set. See tune_threshold.py.
"""

import os

import numpy as np

from . import config

_local_model = None


def embedding_input(title, body_text, lede_words=None):
    """What we actually embed: the headline plus the opening of the article.

    News writing is an inverted pyramid — the core facts are front-loaded and
    later paragraphs are background. Embedding the whole article dilutes the
    signal about *which event this is* with paragraphs that many unrelated
    stories share.
    """
    lede_words = lede_words or config.LEDE_WORDS
    lede = " ".join((body_text or "").split()[:lede_words])
    return f"{title}\n\n{lede}".strip()


def _embed_local(texts):
    global _local_model
    from sentence_transformers import SentenceTransformer

    if _local_model is None:
        _local_model = SentenceTransformer(config.LOCAL_EMBED_MODEL)
    vectors = _local_model.encode(
        texts,
        batch_size=32,
        convert_to_numpy=True,
        normalize_embeddings=True,   # so cosine similarity == dot product
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def _embed_voyage(texts):
    import voyageai

    client = voyageai.Client()  # reads VOYAGE_API_KEY
    out = []
    # Voyage accepts batches; keep them modest to stay under token limits.
    for i in range(0, len(texts), 64):
        chunk = texts[i:i + 64]
        result = client.embed(chunk, model=config.VOYAGE_EMBED_MODEL,
                              input_type="document")
        out.extend(result.embeddings)
    vectors = np.asarray(out, dtype=np.float32)
    # Voyage vectors are already unit length, but normalise defensively so the
    # dot-product shortcut downstream is always valid.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.clip(norms, 1e-9, None)


def embed(texts, provider=None):
    """texts -> (n, d) float32 array of unit-length vectors."""
    provider = provider or active_provider()
    if provider == "voyage":
        return _embed_voyage(texts)
    return _embed_local(texts)


def active_provider():
    """Voyage if a key is present, otherwise the local model."""
    if config.EMBED_PROVIDER != "auto":
        return config.EMBED_PROVIDER
    return "voyage" if os.environ.get("VOYAGE_API_KEY") else "local"
