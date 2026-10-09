import re
import unittest
from pathlib import Path

from floability_remote.cli import build_parser

from .support import make_client, requires_web


STATIC = (
    Path(__file__).resolve().parents[2] / "src" / "floability_remote" / "web" / "static"
)


@requires_web
class StaticAssetTests(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def test_index_and_referenced_assets_are_served(self):
        index = self.client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertIn("text/html", index.headers["content-type"])
        assets = re.findall(r'(?:href|src)="(/static/[^"]+)"', index.text)
        self.assertGreaterEqual(len(assets), 2)
        for asset in assets:
            with self.subTest(asset=asset):
                self.assertEqual(self.client.get(asset).status_code, 200)

    def test_javascript_modules_resolve(self):
        for module in ("api.js", "connection.js", "form.js", "main.js", "run.js"):
            with self.subTest(module=module):
                response = self.client.get(f"/static/js/{module}")
                self.assertEqual(response.status_code, 200)
                self.assertIn("javascript", response.headers["content-type"])


class FrontendBoundaryTests(unittest.TestCase):
    def test_only_api_module_calls_the_server(self):
        for script in (STATIC / "js").glob("*.js"):
            with self.subTest(script=script.name):
                source = script.read_text()
                calls_server = "fetch(" in source or "EventSource(" in source
                self.assertEqual(calls_server, script.name == "api.js")

    def test_no_inline_scripts_or_external_script_and_style_assets(self):
        html = (STATIC / "index.html").read_text()
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(html, r'<(?:script|img)[^>]+src="https?://')
        self.assertNotRegex(html, r'<link[^>]+href="https?://')

    def test_form_fields_use_api_paths(self):
        html = (STATIC / "index.html").read_text()
        names = set(re.findall(r'name="([^"]+)"', html))
        self.assertTrue(
            {
                "connection.target",
                "connection.identity_file",
                "backpack.repository",
                "backpack.ref",
                "entrypoint",
                "environment.env_name",
                "remote_root",
                "base_dir",
                "data_cache_dir",
                "jupyter_port",
                "local_port",
            }
            <= names
        )

    def test_advanced_options_are_grouped(self):
        html = (STATIC / "index.html").read_text()
        self.assertEqual(html.count('class="advanced-group"'), 4)
        for title in (
            "Remote environment",
            "Storage",
            "Floability options",
            "Interactive session",
        ):
            self.assertIn(title, html)

    def test_hidden_mode_fields_are_disabled(self):
        source = (STATIC / "js" / "form.js").read_text()
        self.assertIn("control.disabled = !visible", source)

    def test_completed_run_panel_has_download_area(self):
        html = (STATIC / "index.html").read_text()
        self.assertIn('id="run-files"', html)
        source = (STATIC / "js" / "run.js").read_text()
        for group in ("Run command", "Workflow and results", "Logs", "Run records"):
            self.assertIn(group, source)
        self.assertNotIn("section.open = true", source)

    def test_sidebar_switches_from_guidance_to_cluster_readiness(self):
        html = (STATIC / "index.html").read_text()
        self.assertNotIn("Web availability", html)
        self.assertIn('id="getting-started-card"', html)
        self.assertIn('href="https://github.com/floability-hub"', html)
        self.assertIn('href="https://floability.readthedocs.io/en/stable/"', html)
        self.assertIn('id="cluster-card" hidden', html)
        source = (STATIC / "js" / "main.js").read_text()
        self.assertIn("renderConnectionSidebar", source)
        self.assertIn("api.checkCluster", source)


class WebCommandParserTests(unittest.TestCase):
    def test_defaults(self):
        args = build_parser().parse_args(["web"])
        self.assertIsNone(args.port)
        self.assertFalse(args.no_browser)

    def test_options(self):
        args = build_parser().parse_args(["web", "--port", "8765", "--no-browser"])
        self.assertEqual(args.port, 8765)
        self.assertTrue(args.no_browser)


if __name__ == "__main__":
    unittest.main()
