"""
Skill 话术路由系统 — 在 LLM 介入之前，优先匹配标准话术模板。
命中后直接返回运营审核过的原文，禁止改写。
"""

import json
import numpy as np
from pathlib import Path
from typing import Optional, Dict, Any


class ScriptRouter:
    """
    话术模板路由器：
    1. 关键词匹配（O(1)，零延迟）
    2. Embedding 相似度兜底（需传入 embedding 模型）
    3. 相似度 > 阈值 → 返回原文；否则 → 返回 None，进入后续流程
    """

    def __init__(self, templates_dir: Path, embeddings=None, threshold: float = 0.85):
        self.threshold = threshold
        self.embeddings = embeddings
        self.scripts = self._load_scripts(templates_dir / "scripts.json")
        self._cached_embeddings = None  # lazy build

    def _load_scripts(self, path: Path) -> list:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("scripts", [])

    def match(self, question: str) -> Optional[Dict[str, Any]]:
        """
        匹配话术模板，返回命中结果或 None。
        结果格式：{"intent": ..., "script": ..., "match_type": "keyword"/"embedding", "score": float}
        """
        # 第一层：关键词直接匹配
        result = self._keyword_match(question)
        if result:
            return result

        # 第二层：Embedding 相似度匹配
        if self.embeddings:
            result = self._embedding_match(question)
            if result:
                return result

        return None

    def _keyword_match(self, question: str) -> Optional[Dict[str, Any]]:
        """关键词匹配：任意关键词命中即返回。"""
        question_lower = question.lower()
        for script in self.scripts:
            for kw in script.get("keywords", []):
                if kw in question_lower or kw in question:
                    return {
                        "intent": script["intent"],
                        "script": script["script"],
                        "match_type": "keyword",
                        "score": 1.0,
                        "template_id": script["id"],
                    }
        return None

    def _embedding_match(self, question: str) -> Optional[Dict[str, Any]]:
        """Embedding 相似度匹配：计算 question 与所有 script 文本的余弦相似度。"""
        if not self.embeddings:
            return None

        # Lazy build: 缓存所有 script 文本的 embedding
        if self._cached_embeddings is None:
            self._build_embedding_cache()

        question_emb = np.array(self.embeddings.embed_query(question))
        question_norm = np.linalg.norm(question_emb)

        best = None
        best_score = 0.0

        for i, cached in enumerate(self._cached_embeddings):
            cached_emb = cached["embedding"]
            cached_norm = cached["norm"]
            cos_sim = np.dot(question_emb, cached_emb) / (question_norm * cached_norm)

            if cos_sim > best_score:
                best_score = cos_sim
                best = self.scripts[i]

        if best_score >= self.threshold and best:
            return {
                "intent": best["intent"],
                "script": best["script"],
                "match_type": "embedding",
                "score": round(float(best_score), 4),
                "template_id": best["id"],
            }

        return None

    def _build_embedding_cache(self):
        """预计算所有 script 文本的 embedding（构造代表性文本用于匹配）。"""
        self._cached_embeddings = []
        for script in self.scripts:
            # 用 keywords + intent 构造匹配文本（而非完整的长 script）
            match_text = f"{script['intent']} {' '.join(script.get('keywords', [])[:5])}"
            emb = np.array(self.embeddings.embed_query(match_text))
            self._cached_embeddings.append({
                "embedding": emb,
                "norm": np.linalg.norm(emb),
            })
        print(f"✅ 话术模板 Embedding 缓存已构建 ({len(self._cached_embeddings)} 条)")
