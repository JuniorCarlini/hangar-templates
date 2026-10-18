"""Unit tests of the version and tag-shape logic of update_tags.py (no network).

Run: python3 -m unittest discover -s scripts
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import update_tags as u  # noqa: E402


def pick(current, tags, rule):
    c = u.candidates(current, tags, rule)
    return c[0].raw if c else None


class ParseTag(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(u.parse_tag("18").nums, (18,))
        self.assertEqual(u.parse_tag("2.43.7").nums, (2, 43, 7))
        t = u.parse_tag("v1.55")
        self.assertEqual((t.prefix, t.nums, t.suffix), ("v", (1, 55), ""))
        t = u.parse_tag("4.3-management")
        self.assertEqual((t.nums, t.suffix), ((4, 3), "-management"))
        self.assertEqual(u.parse_tag("2-rootless").suffix, "-rootless")

    def test_not_versions(self):
        for raw in ["latest", "alpine", "next", "nightly", "sha-abc123", "2024-01-01", "1.0.0-rc.1", "v1.56.0-rc1"]:
            self.assertIsNone(u.parse_tag(raw), raw)

    def test_prereleases_are_not_stable(self):
        for raw in ["1.2-rc1", "3-beta", "4.0-alpha2", "2-dev", "9.0-preview"]:
            self.assertIsNone(u.parse_tag(raw), raw)
        # Words that merely contain those letters are fine.
        self.assertIsNotNone(u.parse_tag("4.3-management"))
        self.assertIsNotNone(u.parse_tag("1.2-archive"))


class Rules(unittest.TestCase):
    TAGS = ["17", "18", "19", "18.1", "18.2", "19.0", "18.1.4", "18.2.0", "latest", "18-alpine", "19-alpine"]

    def test_major_only_tag_keeps_its_shape(self):
        self.assertIsNone(pick("18", self.TAGS, "minor"))
        self.assertIsNone(pick("18", self.TAGS, "patch"))
        self.assertEqual(pick("18", self.TAGS, "major"), "19")

    def test_minor(self):
        self.assertEqual(pick("18.1", self.TAGS, "minor"), "18.2")
        self.assertEqual(pick("18.1", self.TAGS, "major"), "19.0")
        self.assertIsNone(pick("18.1", self.TAGS, "patch"))

    def test_patch(self):
        self.assertEqual(pick("18.1.0", self.TAGS + ["18.1.0"], "patch"), "18.1.4")
        self.assertEqual(pick("18.1.0", self.TAGS + ["18.1.0"], "minor"), "18.2.0")

    def test_keep(self):
        self.assertIsNone(pick("18", self.TAGS, "keep"))

    def test_never_older(self):
        self.assertIsNone(pick("19", self.TAGS, "major"))

    def test_suffix_and_prefix_preserved(self):
        tags = ["4.3-management", "4.4-management", "4.4", "5.0-management", "4.5-management-alpine"]
        self.assertEqual(pick("4.3-management", tags, "minor"), "4.4-management")
        self.assertEqual(pick("4.3-management", tags, "major"), "5.0-management")
        tags = ["2-rootless", "3-rootless", "2.1-rootless", "2", "3"]
        self.assertIsNone(pick("2-rootless", tags, "minor"))
        self.assertEqual(pick("2-rootless", tags, "major"), "3-rootless")
        tags = ["v1.55", "v1.56", "1.57", "v2.0", "v1.56.1", "v1.57.0-rc.0"]
        self.assertEqual(pick("v1.55", tags, "minor"), "v1.56")
        self.assertEqual(pick("v1.55", tags, "major"), "v2.0")

    def test_numeric_order_not_lexical(self):
        self.assertEqual(pick("9.9", ["9.10", "9.9", "9.8"], "minor"), "9.10")

    def test_newest_first(self):
        got = [t.raw for t in u.candidates("1.0.2", ["1.0.3", "1.2.0", "1.1.9", "2.0.0"], "minor")]
        self.assertEqual(got, ["1.2.0", "1.1.9", "1.0.3"])

    def test_unknown_rule(self):
        with self.assertRaises(ValueError):
            u.allowed(u.parse_tag("1"), u.parse_tag("2"), "any")


class Images(unittest.TestCase):
    def test_parse(self):
        i = u.parse_image("postgres:18")
        self.assertEqual((i.registry, i.repo, i.name, i.tag), (u.DOCKER_HUB, "library/postgres", "postgres", "18"))
        i = u.parse_image("getmeili/meilisearch:v1.55")
        self.assertEqual((i.registry, i.repo), (u.DOCKER_HUB, "getmeili/meilisearch"))
        i = u.parse_image("ghcr.io/owner/app:1.2")
        self.assertEqual((i.registry, i.repo, i.tag), ("ghcr.io", "owner/app", "1.2"))
        i = u.parse_image("docker.io/library/redis:8")
        self.assertEqual((i.registry, i.repo, i.name), (u.DOCKER_HUB, "library/redis", "docker.io/library/redis"))
        self.assertIsNone(u.parse_image("postgres"))
        self.assertIsNone(u.parse_image("localhost:5000/app"))
        self.assertIsNone(u.parse_image("postgres:18@sha256:abc"))

    def test_rewrite_keeps_everything_else(self):
        text = (
            "services:\n"
            "  app:\n"
            "    image: n8nio/n8n:2.43.7  # pinned\n"
            "  db:\n"
            "    image: \"postgres:18\"\n"
            "    environment:\n"
            "      X: postgres:18\n"
        )
        out = u.rewrite_tags(text, {"n8nio/n8n:2.43.7": "2.44.0", "postgres:18": "18"})
        self.assertIn("    image: n8nio/n8n:2.44.0  # pinned\n", out)
        self.assertIn("    image: \"postgres:18\"\n", out)
        self.assertIn("      X: postgres:18\n", out)
        self.assertEqual(u.find_images(text), ["n8nio/n8n:2.43.7", "postgres:18"])

    def test_bump_version(self):
        self.assertEqual(u.bump_version('id = "x"\nversion = 2\n[variables.version]\n'), 'id = "x"\nversion = 3\n[variables.version]\n')
        with self.assertRaises(ValueError):
            u.bump_version('id = "x"\n')

    def test_manifest_platforms(self):
        idx = {"manifests": [
            {"platform": {"os": "linux", "architecture": "amd64"}},
            {"platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}},
            {"platform": {"os": "unknown", "architecture": "unknown"}},
        ]}
        self.assertEqual(u.manifest_platforms(idx), {("linux", "amd64"), ("linux", "arm64")})
        self.assertEqual(u.manifest_platforms({"config": {}, "layers": []}), set())


class Policy(unittest.TestCase):
    def test_rules(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "updates.toml"
            p.write_text('default = "patch"\n[templates.n8n]\nrule = "minor"\nimages = { postgres = "keep" }\n')
            pol = u.load_policy(p)
            self.assertEqual(pol.rule("n8n", u.parse_image("n8nio/n8n:1")), "minor")
            self.assertEqual(pol.rule("n8n", u.parse_image("postgres:18")), "keep")
            self.assertEqual(pol.rule("other", u.parse_image("redis:8")), "patch")
            p.write_text('[templates.x]\nrule = "latest"\n')
            with self.assertRaises(ValueError):
                u.load_policy(p)

    def test_repo_policy_is_valid(self):
        u.load_policy(Path(__file__).resolve().parent.parent / "updates.toml")


class Run(unittest.TestCase):
    """run() against a fake registry: rewrite, version bump, idempotence."""

    class FakeRegistry:
        def __init__(self, tags, plats):
            self._tags, self._plats = tags, plats

        def tags(self, img):
            return self._tags[img.repo]

        def platforms(self, img, tag):
            return self._plats.get((img.repo, tag), {("linux", "amd64"), ("linux", "arm64")})

        def digest(self, img, tag):
            return tag

    def test_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            t = root / "templates" / "mq"
            t.mkdir(parents=True)
            (t / "compose.yaml").write_text("services:\n  mq:\n    image: rabbitmq:4.3-management\n")
            (t / "template.toml").write_text('id = "mq"\nversion = 2\n')
            reg = self.FakeRegistry(
                {"library/rabbitmq": ["4.3-management", "4.4-management", "4.5-management", "5.0-management"]},
                {("library/rabbitmq", "4.5-management"): {("linux", "amd64")}},
            )
            pol = u.Policy()
            self.assertEqual(u.run(root, pol, True, reg), ["mq: rabbitmq 4.3-management -> 4.4-management"])
            self.assertIn("4.3-management", (t / "compose.yaml").read_text())  # dry run
            u.run(root, pol, False, reg)
            self.assertIn("image: rabbitmq:4.4-management\n", (t / "compose.yaml").read_text())
            self.assertIn("version = 3", (t / "template.toml").read_text())
            self.assertEqual(u.run(root, pol, False, reg), [])
            self.assertIn("version = 3", (t / "template.toml").read_text())


if __name__ == "__main__":
    unittest.main()
