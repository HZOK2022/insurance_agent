import unittest
from app.retrieval.chunker import chunk_text, chunk_documents


class ChunkerTest(unittest.TestCase):
    def test_chunk_split_and_overlap(self):
        text = "a" * 2500
        cs = chunk_text(text, 1000, 200)
        self.assertEqual(len(cs), 3)          # 1000/1200/1600..2500
        self.assertEqual(cs[0][-200:], cs[1][:200])   # 与下一块重叠
        self.assertEqual(cs[0][:1000], text[:1000])

    def test_chunk_documents_adds_suffix(self):
        docs = [{"text": "x" * 1200, "meta": {"chunk_id": "d"}}]
        out = chunk_documents(docs, 1000, 200)
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0]["meta"]["chunk_id"].endswith(":0"))
        self.assertTrue(out[1]["meta"]["chunk_id"].endswith(":1"))

    def test_empty(self):
        self.assertEqual(chunk_text("", 1000, 200), [])


if __name__ == "__main__":
    unittest.main()
