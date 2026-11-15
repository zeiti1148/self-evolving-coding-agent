"""Optional local-only E5 retrieval and bidirectional NLI. No startup downloads."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock

ROOT = Path(__file__).resolve().parents[2]
EMBEDDING_MODEL = 'intfloat/multilingual-e5-small'
NLI_MODEL = 'MoritzLaurer/mDeBERTa-v3-base-mnli-xnli'


class LocalSemantics:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.environ.get('KNOWLEDGE_MODELS_DIR', ROOT / '.cache/knowledge-models'))
        self.lock = RLock()
        self.encoder = self.tokenizer = self.classifier = None
        self.manifest = {}
        self.calibration = {}
        self.error = None
        self.inference_cache = {}
        self.refresh()

    def refresh(self):
        with self.lock:
            previous=self.manifest
            for filename, attr in [('manifest.json', 'manifest'), ('calibration.json', 'calibration')]:
                try:
                    setattr(self, attr, json.loads((self.directory / filename).read_text(encoding='utf-8')))
                except (OSError, ValueError):
                    setattr(self, attr, {})
            if previous!=self.manifest:
                self.encoder=self.tokenizer=self.classifier=None
                self.inference_cache.clear()

    @property
    def version(self):
        from .relations import RELATION_POLICY_VERSION
        value = json.dumps([self.manifest, self.calibration, RELATION_POLICY_VERSION], sort_keys=True)
        return hashlib.sha256(value.encode()).hexdigest()

    @property
    def available(self):
        return (os.environ.get('KNOWLEDGE_MODELS_ENABLED','true').lower()=='true'
                and bool(self.manifest.get('embedding') and self.manifest.get('nli')))

    def load(self):
        if not self.available:
            return False
        with self.lock:
            if self.classifier is not None:
                return True
            try:
                import torch
                from sentence_transformers import SentenceTransformer
                from transformers import AutoModelForSequenceClassification, AutoTokenizer
                torch.set_num_threads(min(4, os.cpu_count() or 1))
                self.encoder = SentenceTransformer(self.manifest['embedding']['path'], device='cpu', local_files_only=True)
                self.tokenizer = AutoTokenizer.from_pretrained(self.manifest['nli']['path'], local_files_only=True)
                self.classifier = AutoModelForSequenceClassification.from_pretrained(
                    self.manifest['nli']['path'], local_files_only=True, use_safetensors=True).float().eval()
                return True
            except (ImportError, OSError, ValueError, RuntimeError) as exc:
                self.error = type(exc).__name__
                return False

    def embed(self, texts, *, query=False):
        if not self.load():
            return None
        import numpy as np
        with self.lock:
            chunks, owners = [], []
            tokenizer = self.encoder.tokenizer
            for index, text in enumerate(texts):
                tokens = tokenizer.encode(text, add_special_tokens=False)
                for start in range(0, max(1, len(tokens)), 224):
                    chunks.append(('query: ' if query else 'passage: ') + tokenizer.decode(tokens[start:start+256]))
                    owners.append(index)
            vectors = self.encoder.encode(chunks, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
            result = []
            for index in range(len(texts)):
                value = np.mean(vectors[[i for i, owner in enumerate(owners) if owner == index]], axis=0)
                result.append(value / max(float(np.linalg.norm(value)), 1e-12))
            return np.asarray(result, dtype='float32')

    def infer(self, left, right):
        key = (left, right)
        if key in self.inference_cache:
            return self.inference_cache[key]
        if not self.load():
            return None
        import torch
        with self.lock, torch.inference_mode():
            # Full pairs only. A truncated qualifier must never become an automatic decision.
            encoded = self.tokenizer([left, right], [right, left], padding=True, truncation=False, return_tensors='pt')
            if encoded['input_ids'].shape[1] > min(512, self.classifier.config.max_position_embeddings):
                return None
            values = torch.softmax(self.classifier(**encoded).logits, dim=-1).cpu().tolist()
            labels = {int(k): v.lower() for k, v in self.classifier.config.id2label.items()}
            # This model's published label order is entailment, neutral, contradiction.
            if not {'entailment','neutral','contradiction'}.issubset(set(labels.values())):
                labels = {0:'entailment',1:'neutral',2:'contradiction'}
            result = {label: min(row[index] for row in values) for index, label in labels.items()}
            if len(self.inference_cache) >= 4096:
                self.inference_cache.pop(next(iter(self.inference_cache)))
            self.inference_cache[key] = result
            return result

    def threshold(self, relation):
        spec = self.calibration.get(relation, {})
        return float(spec['threshold']) if spec.get('enabled') and spec.get('model_manifest') == self.manifest else None


_runtime = None


def runtime():
    global _runtime
    if _runtime is None:
        _runtime = LocalSemantics()
    return _runtime
