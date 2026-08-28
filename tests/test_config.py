import os, re, unittest
from app.config import Config, load

ALL_KEYS = ["DEEPSEEK_API_KEY","DEEPSEEK_MODEL","EMBEDDING_PROVIDER","EMBEDDING_DIM",
            "MAX_STEPS_PER_TURN","MAX_TOKENS_PER_TURN","TOOL_TIMEOUT_SECONDS",
            "MAX_TOOL_RESULT_CHARS","DAILY_TOKEN_BUDGET_PER_USER","WRITE_TOOLS_APPROVAL",
            "APPROVAL_EXEMPT_TOOLS","INTERNAL_TOKEN","SQLITE_PATH","QDRANT_URL","REDIS_URL"]

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
        for n in ("embedding_dim","max_steps_per_turn","max_tokens_per_turn","tool_timeout_seconds","max_tool_result_chars","daily_token_budget_per_user"):
            self.assertGreater(getattr(cfg, n), 0)
        self.assertEqual(cfg.embedding_provider, "bge_m3")
        self.assertEqual(cfg.max_steps_per_turn, 20)

    def test_load_reads_env_and_coerces(self):
        os.environ["DEEPSEEK_MODEL"] = "deepseek-reasoner"
        os.environ["MAX_STEPS_PER_TURN"] = "42"
        cfg = load()
        self.assertEqual(cfg.deepseek_model, "deepseek-reasoner")
        self.assertEqual(cfg.max_steps_per_turn, 42)

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

    def test_no_redefined_limits_outside_config(self):
        # 集中配置:上限字段名不允许在 config.py 之外被重新定义/赋值
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
