# -*- coding: utf-8 -*-
"""Answer Relevancy 纯函数与主链路单测:eval_relevancy.py 的 cosine/prompt/parse/聚合 用 mock 验证。"""
import json
import unittest
from scripts.eval_relevancy import (cosine, generate_questions_prompt, parse_questions,
                                    eval_relevancy, _mean)


class FakeEmbedder:
    def embed(self, texts):
        vmap = {"query": [1.0, 0.0], "good": [1.0, 0.0], "bad": [0.0, 1.0]}
        return [vmap.get(t, [0.0, 0.0]) for t in texts]


class FakeLLM:
    """按 answer 关键词回固定问题;省略 json_mode/usage 语义,只测链路。"""
    def chat(self, messages, json_mode=True, **kw):
        answer = messages[-1]["content"]
        if "mixed" in answer:
            return json.dumps({"questions": ["good", "bad"]}, ensure_ascii=False), {}
        if "good" in answer:
            return json.dumps({"questions": ["good"]}, ensure_ascii=False), {}
        if "fence" in answer:  # 带 ```json 包裹的容错样本
            return "```json\n{\"questions\": [\"good\"]}\n```", {}
        return "not json", {}


class CosineTest(unittest.TestCase):
    def test_identical_is_1(self):
        self.assertAlmostEqual(cosine([3, 4], [3, 4]), 1.0)

    def test_orthogonal_is_0(self):
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0.0)

    def test_len_mismatch_returns_0(self):
        self.assertEqual(cosine([1], [1, 2]), 0.0)

    def test_zero_vector_returns_0(self):
        self.assertEqual(cosine([0, 0], [1, 1]), 0.0)

    def test_normalizes_scale(self):
        self.assertAlmostEqual(cosine([6, 8], [0.6, 0.8]), 1.0)  # 比例相同=1


class PromptTest(unittest.TestCase):
    def test_contains_answer_and_n(self):
        p = generate_questions_prompt("理赔要材料", 3)
        self.assertIn("理赔要材料", p)
        self.assertIn("3", p)


class ParseQuestionsTest(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(parse_questions('{"questions": ["a", " b "]}'), ["a", "b"])

    def test_fenced_markdown(self):
        self.assertEqual(parse_questions('```json\n{"questions": ["a"]}\n```'), ["a"])

    def test_extra_text_around(self):
        self.assertEqual(parse_questions('别的话 {"questions": ["a"]} 结尾'), ["a"])

    def test_garbage_returns_empty(self):
        self.assertEqual(parse_questions("totally broken"), [])
        self.assertEqual(parse_questions(""), [])

    def test_filters_blank(self):
        self.assertEqual(parse_questions('{"questions": ["a", "  "]}'), ["a"])


class RelevancyLinkTest(unittest.TestCase):
    def test_single_relevant(self):
        r = eval_relevancy(FakeEmbedder(), FakeLLM(), "query", "good_answer", 1)
        self.assertEqual(r["relevancy"], 1.0, "query 与反推出的 good 余弦=1")

    def test_mixed_mean(self):
        r = eval_relevancy(FakeEmbedder(), FakeLLM(), "query", "mixed_answer", 2)
        self.assertEqual(r["relevancy"], 0.5, "good(1)+bad(0) 均值=0.5")

    def test_empty_answer_returns_none(self):
        self.assertIsNone(eval_relevancy(FakeEmbedder(), FakeLLM(), "query", "", 1))
        self.assertIsNone(eval_relevancy(FakeEmbedder(), FakeLLM(), "", "x", 1))

    def test_parse_failure_returns_none_relevancy(self):
        r = eval_relevancy(FakeEmbedder(), FakeLLM(), "query", "other", 1)
        self.assertIsNone(r["relevancy"])
        self.assertEqual(r["error"], "parse_empty")

    def test_mean(self):
        self.assertEqual(_mean([0.5, 1.0]), 0.75)
        self.assertIsNone(_mean([]))


if __name__ == "__main__":
    unittest.main()