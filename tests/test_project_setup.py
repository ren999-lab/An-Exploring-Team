"""Regression checks for teammate-friendly Streamlit setup files."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestProjectSetup(unittest.TestCase):
    def test_streamlit_config_disables_usage_statistics(self):
        config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")

        self.assertIn("[browser]", config)
        self.assertIn("gatherUsageStats = false", config)

    def test_launcher_uses_project_python_not_anaconda_base(self):
        launcher = (ROOT / "启动UI.bat").read_text(encoding="utf-8")

        self.assertIn(".conda\\python.exe", launcher)
        self.assertIn("-m streamlit run", launcher)
        self.assertIn("app.py", launcher)


if __name__ == "__main__":
    unittest.main()
