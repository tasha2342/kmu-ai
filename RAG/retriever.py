import json
import os

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

from app.config import EMBED_MODEL_ID, INDEX_DIR


class Retriever:
    def __init__(self):
        with open(os.path.join(INDEX_DIR, "chunks.jsonl"), encoding="utf-8") as f:
            self.chunks = [json.loads(line) for line in f]
        self.embeddings = np.load(os.path.join(INDEX_DIR, "embeddings.npy"))

        self.tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL_ID)
        self.model = AutoModel.from_pretrained(EMBED_MODEL_ID)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = self.model.to(self.device).eval()

    @torch.no_grad()
    def _embed_query(self, query):
        encoded = self.tokenizer(
            "query: " + query, padding=True, truncation=True, max_length=512, return_tensors="pt"
        ).to(self.device)
        output = self.model(**encoded)
        mask = encoded["attention_mask"].unsqueeze(-1).to(output.last_hidden_state.dtype)
        summed = (output.last_hidden_state * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1e-9)
        pooled = summed / counts
        pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        return pooled.cpu().numpy()[0]

    def retrieve(self, query, k=4):
        q_emb = self._embed_query(query)
        scores = self.embeddings @ q_emb
        top_idx = np.argsort(-scores)[:k]
        return [{**self.chunks[i], "score": float(scores[i])} for i in top_idx]
