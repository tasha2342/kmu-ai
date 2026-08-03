import json
import os
import subprocess

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

from app.config import CHUNK_OVERLAP, CHUNK_SIZE, DATA_DIR, EMBED_MODEL_ID, INDEX_DIR


def extract_text(hwp_path):
    result = subprocess.run(
        ["hwp5txt", hwp_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"hwp5txt exited with code {result.returncode}")
    return result.stdout


def chunk_text(text, source):
    text = text.strip()
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        piece = text[start:end].strip()
        if piece:
            chunks.append({"source": source, "text": piece})
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


def mean_pool(last_hidden_state, attention_mask):
    mask = attention_mask.unsqueeze(-1).to(last_hidden_state.dtype)
    summed = (last_hidden_state * mask).sum(dim=1)
    counts = mask.sum(dim=1).clamp(min=1e-9)
    return summed / counts


@torch.no_grad()
def embed_texts(texts, tokenizer, model, prefix, batch_size=16):
    device = model.device
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = [prefix + t for t in texts[i:i + batch_size]]
        encoded = tokenizer(
            batch, padding=True, truncation=True, max_length=512, return_tensors="pt"
        ).to(device)
        output = model(**encoded)
        pooled = mean_pool(output.last_hidden_state, encoded["attention_mask"])
        pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        all_embeddings.append(pooled.cpu().numpy())
    return np.concatenate(all_embeddings, axis=0)


def _source_manifest(hwp_files):
    return {f: os.path.getmtime(os.path.join(DATA_DIR, f)) for f in hwp_files}


def index_is_stale():
    """True if RAG/data has files added/removed/modified since the index was built."""
    manifest_path = os.path.join(INDEX_DIR, "manifest.json")
    embeddings_path = os.path.join(INDEX_DIR, "embeddings.npy")
    if not os.path.exists(embeddings_path) or not os.path.exists(manifest_path):
        return True

    hwp_files = sorted(f for f in os.listdir(DATA_DIR) if f.lower().endswith(".hwp"))
    with open(manifest_path, encoding="utf-8") as f:
        saved_manifest = json.load(f)
    return saved_manifest != _source_manifest(hwp_files)


def build_index():
    os.makedirs(INDEX_DIR, exist_ok=True)

    hwp_files = sorted(f for f in os.listdir(DATA_DIR) if f.lower().endswith(".hwp"))
    if not hwp_files:
        raise RuntimeError(f"No .hwp files found in {DATA_DIR}")
    manifest = _source_manifest(hwp_files)

    all_chunks = []
    failed = []
    for fname in hwp_files:
        path = os.path.join(DATA_DIR, fname)
        print(f"[ingest] extracting: {fname}")
        try:
            text = extract_text(path)
        except Exception as e:
            print(f"[ingest]   !! extraction failed, skipping: {e}")
            failed.append(fname)
            continue
        chunks = chunk_text(text, fname)
        print(f"[ingest]   -> {len(chunks)} chunks")
        all_chunks.extend(chunks)

    if failed:
        print(f"[ingest] WARNING: skipped {len(failed)} file(s) that failed extraction: {', '.join(failed)}")
    if not all_chunks:
        raise RuntimeError("No chunks extracted from any document; aborting index build")

    print(f"[ingest] total chunks: {len(all_chunks)}")
    print(f"[ingest] loading embedding model: {EMBED_MODEL_ID}")
    tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL_ID)
    model = AutoModel.from_pretrained(EMBED_MODEL_ID)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device).eval()

    texts = [c["text"] for c in all_chunks]
    embeddings = embed_texts(texts, tokenizer, model, prefix="passage: ")

    np.save(os.path.join(INDEX_DIR, "embeddings.npy"), embeddings)
    with open(os.path.join(INDEX_DIR, "chunks.jsonl"), "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with open(os.path.join(INDEX_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    print(f"[ingest] saved index to {INDEX_DIR}")


if __name__ == "__main__":
    build_index()
