import os, re, tempfile, unittest
from app.config import Config, load, load_dotenv

ALL_KEYS = ["DEEPSEEK_API_KEY","DEEPSEEK_BASE_URL","DEEPSEEK_MODEL","DEEPSEEK_TEMPERATURE","DEEPSEEK_MAX_TOKENS",
            "EMBEDDING_MODEL","EMBEDDING_DEVICE","EMBEDDING_BATCH_SIZE","EMBEDDING_DIM",
            "TEXT_SPLITTER","CHUNK_SIZE","CHUNK_OVERLAP","TOP_K","TOP_K_RERANKER",
            "RELEVANCE_THRESHOLD","HYBRID_BM25_WEIGHT","RERANKING_ENGINE","RERANKING_EXTERNAL_URL",
            "RERANKING_EXTERNAL_API_KEY","RERANKING_EXTERNAL_MODEL","RERANKING_EXTERNAL_TIMEOUT","RERANKING_MAX_LENGTH",
            "MAX_STEPS_PER_TURN","MAX_TOKENS_PER_TURN","TOOL_TIMEOUT_SECONDS","MAX_TOOL_RESULT_CHARS",
            "DAILY_TOKEN_BUDGET_PER_USER","WRITE_TOOLS_APPROVAL","APPROVAL_EXEMPT_TOOLS","INTERNAL_TOKEN",
            "SQLITE_PATH","QDRANT_URL","QDRANT_COLLECTION","REDIS_URL"]

LIMIT_NAMES = ("max_steps_per_turn","max_tokens_per_turn","tool_timeout_seconds",
               "max_tool_result_chars","daily_token_budget_per_user","write_tools_approval")

class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ALL_KEYS}
        for k in ALL_KEYS: os.environ.pop(k, None)
    def tearDown(self):
        for k in ALL_KEYS:
            v = self.saved.get(k)
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def test_defaults_present_and_positive(self):
        cfg = Config()
        for n in ("chunk_size","top_k","top_k_reranker","embedding_batch_size","max_steps_per_turn","max_tokens_per_turn","tool_timeout_seconds","max_tool_result_chars","daily_token_budget_per_user"):
            self.assertGreater(getattr(cfg, n), 0)
        self.assertGreaterEqual(cfg.chunk_overlap, 0)
        self.assertEqual(cfg.chunk_size, 1000)
        self.assertEqual(cfg.top_k, 20)
        self.assertEqual(cfg.embedding_dim, 1024)

    def test_load_reads_env_and_coerces(self):
        os.environ["DEEPSEEK_MODEL"] = "deepseek-reasoner"
        os.environ["MAX_STEPS_PER_TURN"] = "42"
        cfg = load()
        self.assertEqual(cfg.deepseek_model, "deepseek-reasoner")
        self.assertEqual(cfg.max_steps_per_turn, 42)

    def test_float_coerce_and_range(self):
        os.environ["HYBRID_BM25_WEIGHT"] = "0.8"
        self.assertAlmostEqual(load().hybrid_bm25_weight, 0.8)
        os.environ["HYBRID_BM25_WEIGHT"] = "1.5"
        with self.assertRaises(ValueError):
            load()

    def test_non_integer_raises(self):
        os.environ["MAX_STEPS_PER_TURN"] = "abc"
        with self.assertRaises(ValueError):
            load()

    def test_negative_threshold_raises(self):
        os.environ["MAX_TOKENS_PER_TURN"] = "-5"
        with self.assertRaises(ValueError):
            load()

    def test_exempt_tools_parsed(self):
        os.environ["APPROVAL_EXEMPT_TOOLS"] = " search_knowledge ,tool-web "
        cfg = load()
        self.assertEqual(cfg.approval_exempt_tools, ("search_knowledge", "tool-web"))

    def test_load_dotenv(self):
        dir_ = tempfile.mkdtemp()
        p = os.path.join(dir_, ".env")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# comment\nQDRANT_COLLECTION=mycoll\nREDIS_URL=redis://x:1\n")
        os.environ.pop("QDRANT_COLLECTION", None); os.environ.pop("REDIS_URL", None)
        load_dotenv(p)
        self.assertEqual(os.environ.get("QDRANT_COLLECTION"), "mycoll")
        self.assertEqual(os.environ.get("REDIS_URL"), "redis://x:1")

    def test_no_redefined_limits_outside_config(self):
        app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
        for root, _, files in os.walk(app_dir):
            for fn in files:
                if not fn.endswith(".py"): continue
                p = os.path.join(root, fn)
                if fn == "config.py": continue
                with open(p, encoding="utf-8") as f: src = f.read()
                for name in LIMIT_NAMES:
                    m = re.search(r"\b" + name + r"\s*=", src)
                    self.assertIsNone(m, f"{fn} 重新定义了配置项 {name}")

if __name__ == "__main__":
    unittest.main()
