"""Tests for scripts/doctidy.py. Standard library only.

Run from anywhere:
    python3 -B -m unittest discover -s <skill-or-plugin-root>/tests -v

Every Markdown fixture is written into a temporary directory by the tests
themselves. None is committed: a committed fixture pair would be read as a
real pair by the documentation checks that run over this repository, and a
fixture with front matter would be picked up by frontmatter linters.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import doctidy  # noqa: E402

GIT = shutil.which("git")
HEADER = "\t".join(doctidy.RULE_COLUMNS)
NOW = "2026-09-17"


def rule_row(rule_id="r1", kind="byproduct", scope="name", strength="strong", regex=r"(?:^|-)prompt$",
             example="x-prompt.md", counter="prompter.md", note="note") -> str:
    return "\t".join((rule_id, kind, scope, strength, regex, example, counter, note))


class RulesDir:
    """A throwaway rules directory."""

    def __init__(self, rows: list[str] | None = None, local: list[str] | None = None, raw: str | None = None):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        body = raw if raw is not None else "\n".join([HEADER, *(rows or [])]) + "\n"
        (self.path / "rules.tsv").write_text(body, encoding="utf-8")
        if local is not None:
            (self.path / "rules.local.tsv").write_text("\n".join([HEADER, *local]) + "\n", encoding="utf-8")

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


class Workspace(unittest.TestCase):
    """Base class: a temporary directory and a git identity isolated from the user's config."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        home = self.tmp / "_home"
        home.mkdir()
        env = {
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(home),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        self.ruleset, problems = doctidy.load_rules(doctidy.RULES_DIR)
        self.assertEqual(problems, [])

    def repo(self, name: str = "repo", files: dict[str, str] | None = None, git: bool = True,
             untracked: dict[str, str] | None = None, when: str = "2026-01-01T00:00:00") -> Path:
        root = self.tmp / name
        root.mkdir(parents=True)
        if git:
            self.skip_without_git()
            self.git(root, "init", "-q")
            self.git(root, "symbolic-ref", "HEAD", "refs/heads/main")
        for rel, text in (files or {}).items():
            self.write(root, rel, text)
        if git and files:
            self.commit(root, when)
        for rel, text in (untracked or {}).items():
            self.write(root, rel, text)
        return root

    def skip_without_git(self) -> None:
        if GIT is None:
            self.skipTest("git is not installed")

    @staticmethod
    def write(root: Path, rel: str, text: str) -> Path:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def git(root: Path, *args: str, when: str | None = None) -> str:
        env = dict(os.environ)
        if when:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env, check=True)
        return result.stdout

    def commit(self, root: Path, when: str = "2026-01-01T00:00:00", message: str = "commit",
               paths: list[str] | None = None) -> None:
        self.git(root, "add", "-A", *(["--", *paths] if paths else []))
        self.git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message, "--allow-empty", when=when)

    def scan(self, root: Path, **options) -> tuple[doctidy.Repo, list[dict], dict]:
        opts = doctidy.ScanOptions(now=doctidy.parse_date(NOW), **options)
        repo = doctidy.analyze(root, self.ruleset, opts)
        findings = doctidy.collect_findings(repo, opts)
        report = doctidy.scan_report(repo, self.ruleset, opts, findings, None, "slim")
        return repo, findings, report

    @staticmethod
    def codes(findings: list[dict], code: str) -> list[dict]:
        return [item for item in findings if item["code"] == code]


def run_main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = doctidy.main(argv)
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# A. Rules
# ---------------------------------------------------------------------------

class ShippedRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ruleset, self.problems = doctidy.load_rules(doctidy.RULES_DIR)

    def test_shipped_rules_are_valid(self) -> None:
        self.assertEqual(self.problems, [])
        self.assertFalse(self.ruleset.local and not (doctidy.RULES_DIR / "rules.local.tsv").exists())

    def name_hits(self, name: str) -> list[tuple[str, str, str]]:
        key = doctidy.name_key(name)
        return [(r.id, r.kind, r.strength) for kind in ("byproduct", "historical")
                for r in self.ruleset.active(kind, "name") if r.regex.search(key)]

    def test_real_world_false_positives_stay_quiet(self) -> None:
        for name in ("bug_report.md", "k8s-reviewer.md", "plan-update.md", "explanation.md",
                     "03_status_effects.md", "session-handoff-prompter.md", "release-notes.md",
                     "capacity-planning.md", "argo-rollouts.md", "incidental-costs.md"):
            with self.subTest(name=name):
                self.assertEqual(self.name_hits(name), [])

    def test_real_world_byproducts_are_strong(self) -> None:
        for name in ("alarm-followup-prompt-en.md", "billing-ci-handoff.md", "vsc-extension-quickstart.md",
                     "IMPLEMENTATION_SUMMARY.md"):
            with self.subTest(name=name):
                self.assertIn(("byproduct", "strong"), [(kind, strength) for _, kind, strength in self.name_hits(name)])

    def test_historical_names_are_weak(self) -> None:
        for name in ("alarm-incident-2026-04-23.md", "sso-rollout-2026-04.md", "S3-migration-plan.md", "status.md"):
            with self.subTest(name=name):
                self.assertEqual({(k, s) for _, k, s in self.name_hits(name)}, {("historical", "weak")})


class RuleValidationTest(unittest.TestCase):
    def problems_for(self, rows: list[str] | None = None, raw: str | None = None, local: list[str] | None = None) -> list[str]:
        with RulesDir(rows, local=local, raw=raw) as directory:
            return doctidy.load_rules(directory)[1]

    def test_each_validation_error(self) -> None:
        cases = {
            "header must be": dict(raw="id\tkind\n"),
            "expected 8 columns": dict(rows=["a\tb\tc"]),
            "empty id": dict(rows=[rule_row(rule_id="")]),
            "duplicate id": dict(rows=[rule_row(), rule_row()]),
            "kind 'nope'": dict(rows=[rule_row(kind="nope")]),
            "scope 'slug' is not allowed": dict(rows=[rule_row(scope="slug")]),
            "strength 'medium'": dict(rows=[rule_row(strength="medium")]),
            "syntax Python 3.10 rejects": dict(rows=[rule_row(regex="(?>prompt)")]),
            "global inline flag": dict(rows=[rule_row(regex="(?i)prompt$")]),
            "does not compile": dict(rows=[rule_row(regex="(prompt")]),
            "no example": dict(rows=[rule_row(example="")]),
            "does not match its regex": dict(rows=[rule_row(example="notes.md")]),
            "counter 'x-prompt.md' matches": dict(rows=[rule_row(counter="x-prompt.md")]),
            "needs counter examples": dict(rows=[rule_row(counter="")]),
            "missing header row": dict(raw="# only a comment\n"),
        }
        for expected, kwargs in cases.items():
            with self.subTest(expected=expected):
                self.assertTrue(any(expected in p for p in self.problems_for(**kwargs)), self.problems_for(**kwargs))

    def test_possessive_quantifier_rejected(self) -> None:
        self.assertTrue(any("3.10" in p for p in self.problems_for([rule_row(regex="prompt+$", example="x-promptt.md")]) +
                            self.problems_for([rule_row(regex="promp.*+", example="x-prompt.md")])))

    def test_cross_checks(self) -> None:
        clash = self.problems_for([
            rule_row(),
            rule_row(rule_id="root", kind="root-allow", regex="^(?:x-prompt)$", example="x-prompt.md", counter=""),
        ])
        self.assertTrue(any("also a strong byproduct" in p for p in clash))
        backup = self.problems_for([
            rule_row(rule_id="arch", kind="archive-dir", scope="dir", regex="^_backup$", example="_backup", counter=""),
            rule_row(rule_id="bak", kind="backup-dir", scope="dir", regex="^_backup$", example="_backup", counter=""),
        ])
        self.assertTrue(any("also a backup directory" in p for p in backup))

    def test_overlay_replaces_disables_and_appends(self) -> None:
        with RulesDir([rule_row(), rule_row(rule_id="r2", regex="(?:^|-)handoff$", example="x-handoff.md", counter="h.md")],
                      local=[rule_row(note="replaced"), rule_row(rule_id="r2", strength="off", regex="(?:^|-)handoff$",
                                                                  example="x-handoff.md", counter="h.md"),
                             rule_row(rule_id="r3", regex="(?:^|-)scratch$", example="x-scratch.md", counter="s.md")]) as directory:
            ruleset, problems = doctidy.load_rules(directory)
        self.assertEqual(problems, [])
        self.assertTrue(ruleset.local)
        self.assertEqual(ruleset.overridden, ["r1", "r2"])
        self.assertEqual(ruleset.rules["r1"].note, "replaced")
        self.assertEqual(ruleset.disabled, ["r2"])
        self.assertEqual([r.id for r in ruleset.active("byproduct")], ["r1", "r3"])
        self.assertEqual(ruleset.describe()["disabled"], ["r2"])

    def test_missing_rules_file(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(doctidy.RulesError):
                doctidy.load_rules(Path(empty))
            code, _, err = run_main(["--rules", empty, "--check-rules"])
        self.assertEqual(code, doctidy.EXIT_USAGE)
        self.assertIn("rules file not found", err)

    def test_check_rules_cli(self) -> None:
        code, out, _ = run_main(["--check-rules"])
        self.assertEqual((code, out.strip()), (0, "rules OK: 34 rows"))
        with RulesDir([rule_row(example="notes.md")]) as directory:
            code, out, _ = run_main(["--rules", str(directory), "--check-rules"])
        self.assertEqual(code, doctidy.EXIT_FINDINGS)
        self.assertIn("does not match", out)
        with RulesDir([rule_row()], local=[rule_row(note="x")]) as directory:
            code, out, _ = run_main(["--rules", str(directory), "--check-rules"])
        self.assertIn("(1 local overrides)", out)

    def test_broken_rules_block_a_scan(self) -> None:
        with RulesDir([rule_row(example="notes.md")]) as directory, tempfile.TemporaryDirectory() as target:
            code, _, err = run_main(["--rules", str(directory), "scan", target])
        self.assertEqual(code, doctidy.EXIT_USAGE)
        self.assertIn("run --check-rules", err)


# ---------------------------------------------------------------------------
# B. Normalisation
# ---------------------------------------------------------------------------

class NameKeyTest(unittest.TestCase):
    def test_name_key(self) -> None:
        cases = {
            "IMPLEMENTATION_SUMMARY-en.md": "implementation-summary",
            "AssetCurationReport.md": "asset-curation-report",
            "ghost-alarm-incident-2026-04-23-en.md": "ghost-alarm-incident",
            "03_status_effects.md": "status-effects",
            "README-KR.md": "readme",
            "guide_kr.md": "guide",
            "guide.ko.md": "guide",
            "guide-zh-cn.md": "guide",
            "sso-rollout-2026-04.md": "sso-rollout",
            "notes-20260423.md": "notes",
            "2026-04-23-launch.md": "launch",
            "README (1).md": "readme-(1)",
            "CLAUDE.local.md": "claude-local",
            "docs/_Sidebar.md": "sidebar",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(doctidy.name_key(name), expected)

    def test_norm_text_and_subjects(self) -> None:
        self.assertEqual(doctidy.norm_text("  **Bold**   `code` ~~x~~ "), "Bold code x")
        self.assertEqual(doctidy.normalize_subject("dir", "_Deprecated"), "_deprecated")
        self.assertEqual(doctidy.normalize_subject("slug", "Foo"), "Foo")
        self.assertEqual(doctidy.strip_doc_extension("x.MARKDOWN"), "x")


# ---------------------------------------------------------------------------
# C. Markdown parser
# ---------------------------------------------------------------------------

class ParserTest(unittest.TestCase):
    def test_fences_hide_headings_and_links(self) -> None:
        parsed = doctidy.parse_markdown(
            "```bash\n# not a title\n[x](gone.md)\n```\n"
            "~~~~\n```\n# still code\n~~~~\n"
            "# Real Title\n\n## Section\n")
        self.assertEqual(parsed.title, {"text": "Real Title", "line": 9, "from": "atx"})
        self.assertEqual(parsed.sections, [{"line": 11, "text": "Section"}])
        self.assertEqual(parsed.links, [])

    def test_backtick_info_string_with_backtick_is_not_a_fence(self) -> None:
        parsed = doctidy.parse_markdown("``` not `a` fence\n# Title\n")
        self.assertEqual(parsed.title["text"], "Title")

    def test_setext_and_frontmatter_titles(self) -> None:
        self.assertEqual(doctidy.parse_markdown("Setext Title\n===\n").title,
                         {"text": "Setext Title", "line": 1, "from": "setext"})
        parsed = doctidy.parse_markdown("---\ntitle: 'It''s here'\nlayout: post\nlink: [a](b.md)\n---\nbody\n")
        self.assertEqual(parsed.title["text"], "It's here")
        self.assertEqual(parsed.frontmatter, ["title", "layout", "link"])
        self.assertEqual(parsed.links, [])

    def test_comments_hide_links(self) -> None:
        parsed = doctidy.parse_markdown("a <!-- [x](one.md) --> b\n<!--\n[y](two.md)\n-->[z](three.md)\n")
        self.assertEqual([link.raw for link in parsed.links], ["three.md"])

    def test_link_forms_and_columns(self) -> None:
        line = ('[a](a.md "title") ![img](<img dir/p.png>) [n](x_(y).md) `code [c](c.md)` '
                '<a href="h.md">h</a> `docs/m.md#part` [bad](my file.md)')
        text = f"{line}\n[ref]: <ref file.md>\n[ref2]: plain.md\n"
        parsed = doctidy.parse_markdown(text)
        forms = [(link.form, link.raw) for link in parsed.links]
        self.assertEqual(forms, [
            ("inline", "a.md"), ("image", "img dir/p.png"), ("inline", "x_(y).md"),
            ("html", "h.md"), ("mention", "docs/m.md#part"),
            ("refdef", "ref file.md"), ("refdef", "plain.md"),
        ])
        lines = doctidy.split_lines(text)
        for link in parsed.links:
            self.assertEqual(lines[link.line - 1][link.col:link.end], link.raw)
        self.assertTrue(parsed.links[1].bracketed)

    def test_unbalanced_and_titled_destinations(self) -> None:
        links = doctidy.parse_markdown("[a](b.md 'single') [c](d.md (paren)) [e](broken [f](\n").links
        self.assertEqual([link.raw for link in links], ["b.md", "d.md"])
        self.assertEqual(doctidy.parse_markdown("[a](<unclosed\n").links, [])
        self.assertEqual(doctidy.parse_markdown("[a](x.md 'unclosed\n").links, [])
        self.assertEqual(doctidy.parse_markdown("text](x.md)\n").links, [])
        self.assertEqual(doctidy.parse_markdown("[a](  )\n").links, [])

    def test_keep_marker_head_and_language(self) -> None:
        parsed = doctidy.parse_markdown("<!-- doc-tidy: keep -->\n# 제목\n한국어 문장입니다.\n| a | b |\n")
        self.assertTrue(parsed.keep)
        self.assertGreater(parsed.hangul_ratio, 0.3)
        self.assertEqual(parsed.table_rows, [(4, "| a | b |")])
        self.assertFalse(doctidy.parse_markdown("\n" * 25 + "<!-- doc-tidy: keep -->\n").keep)
        self.assertEqual(doctidy.parse_markdown("").hangul_ratio, 0.0)

    def test_split_lines_and_scalars(self) -> None:
        self.assertEqual(doctidy.split_lines("a\r\nb\x0cc\n"), ["a", "b\x0cc"])
        self.assertEqual(doctidy.unquote_scalar('"x"'), "x")
        self.assertEqual(doctidy.unquote_scalar("plain"), "plain")


# ---------------------------------------------------------------------------
# D. Resolution
# ---------------------------------------------------------------------------

class ResolveTest(Workspace):
    def build(self) -> doctidy.Repo:
        root = self.repo(files={
            "README.md": "# Root\n",
            "docs/guide.md": "# Guide\n",
            "docs/한글 문서.md": "# 한글\n",
            "docs/sub/README.md": "# Sub\n",
            "assets/logo.png": "png",
            "unique/elsewhere.md": "# Elsewhere\n",
            "Mixed/Case.md": "# Case\n",
        })
        self.write(root, "ignored.log", "x")
        self.write(root, ".gitignore", "*.log\n")
        return doctidy.analyze(root, self.ruleset, doctidy.ScanOptions())

    def test_resolution_cases(self) -> None:
        repo = self.build()
        resolve = doctidy.resolve
        self.assertEqual(resolve(repo, "docs/guide.md", "../README.md", "inline").doc, "README.md")
        self.assertEqual(resolve(repo, "docs/guide.md", "./sub/", "inline").doc, "docs/sub/README.md")
        self.assertEqual(resolve(repo, "docs/guide.md", "/assets/logo.png", "image").status, "file")
        self.assertEqual(resolve(repo, "README.md", "docs/%ED%95%9C%EA%B8%80%20%EB%AC%B8%EC%84%9C.md", "inline").doc,
                         "docs/한글 문서.md")
        nfd = "docs/" + __import__("unicodedata").normalize("NFD", "한글 문서.md")
        self.assertEqual(resolve(repo, "README.md", nfd, "inline").doc, "docs/한글 문서.md")
        self.assertEqual(resolve(repo, "README.md", "docs/guide.md/", "inline").hint, "trailing-slash")
        case = resolve(repo, "README.md", "mixed/case.md", "inline")
        self.assertEqual((case.status, case.hint, case.candidate), ("broken", "case-mismatch", "Mixed/Case.md"))
        moved = resolve(repo, "README.md", "elsewhere.md", "inline")
        self.assertEqual((moved.hint, moved.candidate), ("moved-candidate", "unique/elsewhere.md"))
        self.assertEqual(resolve(repo, "README.md", "../outside.md", "inline").status, "outside")
        self.assertEqual(resolve(repo, "README.md", "/Users/you/x.md", "inline").hint, "absolute-fs-path")
        self.assertEqual(resolve(repo, "README.md", "ignored.log", "inline").status, "ignored")
        self.assertEqual(resolve(repo, "README.md", "gone.md", "inline").hint, None)
        for skipped in ("https://example.com", "#anchor", "//cdn/x.md", "{{ site.url }}/a.md", "", "?q=1"):
            self.assertEqual(resolve(repo, "README.md", skipped, "inline").status, "skip", skipped)
        self.assertEqual(resolve(repo, "docs/guide.md", "..", "inline").doc, "README.md")

    def test_mentions(self) -> None:
        repo = self.build()
        resolve = doctidy.resolve
        self.assertEqual(resolve(repo, "docs/sub/README.md", "../guide.md", "mention").style, "relative")
        self.assertEqual(resolve(repo, "Mixed/Case.md", "docs/guide.md", "mention").style, "root")
        self.assertEqual(resolve(repo, "README.md", f"{repo.root.name}/docs/guide.md", "mention").style, "prefixed")
        basename = resolve(repo, "README.md", "elsewhere.md", "mention")
        self.assertEqual((basename.doc, basename.style), ("unique/elsewhere.md", "basename"))
        self.assertEqual(resolve(repo, "README.md", f"{repo.root}/docs/guide.md", "mention").style, "absolute")
        self.assertEqual(resolve(repo, "README.md", "/elsewhere/else.md", "mention").status, "skip")
        self.assertIsNone(resolve(repo, "README.md", "assets/logo.png", "mention").doc)
        self.assertEqual(resolve(repo, "README.md", "nowhere.md", "mention").status, "skip")
        with mock.patch.dict(os.environ, {"HOME": str(repo.root.parent)}):
            home = resolve(repo, "README.md", f"~/{repo.root.name}/docs/guide.md", "mention")
        self.assertEqual((home.doc, home.style), ("docs/guide.md", "home"))

    def test_site_and_wiki_repos_skip_routed_links(self) -> None:
        site = doctidy.analyze(self.repo("site", {"_config.yml": "x", "index.md": "[a](/about/) [b](about)\n"}),
                               self.ruleset, doctidy.ScanOptions())
        self.assertEqual((site.kind, site.site), ("site", "jekyll"))
        self.assertEqual(site.docs["index.md"].broken, [])
        wiki = doctidy.analyze(self.repo("x.wiki", {"Home.md": "[p](Some-Page)\n"}), self.ruleset, doctidy.ScanOptions())
        self.assertEqual(wiki.kind, "wiki")
        self.assertEqual(wiki.docs["Home.md"].broken, [])


# ---------------------------------------------------------------------------
# E. Discovery and classes
# ---------------------------------------------------------------------------

class DiscoveryTest(Workspace):
    def test_tracking_state(self) -> None:
        root = self.repo(files={"README.md": "# R\n", "docs/a.md": "# A\n"}, untracked={"docs/new.md": "# New\n"})
        self.write(root, "docs/a.md", "# A changed\n")
        self.write(root, "skip.md", "# ignored\n")
        self.write(root, ".gitignore", "skip.md\n")
        repo = doctidy.analyze(root, self.ruleset, doctidy.ScanOptions())
        self.assertEqual(repo.docs["docs/new.md"].delete_risk, "untracked")
        self.assertEqual(repo.docs["docs/a.md"].delete_risk, "uncommitted")
        self.assertEqual(repo.docs["README.md"].delete_risk, "none")
        self.assertNotIn("skip.md", repo.docs)
        self.assertTrue(repo.head)

    def test_exclusions(self) -> None:
        root = self.repo(files={
            "modules/vpc/v5.3.1/README.md": "#", "modules/vpc/v1.2.3-beta/README.md": "#",
            "node_modules/pkg/README.md": "#", "lib/pkg.dist-info/METADATA.md": "#",
            "home/plugins/cache/x/README.md": "#", ".claude/worktrees/w/README.md": "#",
            "modules/vpc/2.0/README.md": "#", "generated/out.md": "#", "README.md": "#",
        })
        (root / "link.md").symlink_to(root / "README.md")
        repo = doctidy.analyze(root, self.ruleset, doctidy.ScanOptions(excludes=["generated/*"]))
        self.assertEqual(repo.excluded, {"skipDirs": 4, "vendoredSemver": 2, "exclude": 1, "symlinks": 1})
        self.assertIn("modules/vpc/2.0/README.md", repo.docs)

    def test_classes(self) -> None:
        root = self.repo(files={
            "README.md": "# R\n", "CLAUDE.md": "See `docs/agent-only.md`.\n", "CHANGELOG.md": "#",
            ".claude/agents/reviewer.md": "---\nname: r\n---\n", "docs/agent-only.md": "# Agent only\n",
            ".github/ISSUE_TEMPLATE/bug_report.md": "#", "tests/fixtures/sample.md": "# fixture\n",
            "_deprecated/old-tool/README.md": "#", "releases/v1.md": "#", "notes.md": "# Notes\n",
            "plugins/p/.claude-plugin/plugin.json": "{}", "plugins/p/commands-ko/run.md": "#",
            "plugins/p/README.md": "#", "cfg/agents/a.md": "#", "cfg/plans/p.md": "#", "cfg/memory/m.md": "#",
            "cfg/CLAUDE.md": "#", "cfg/projects/x/memory/n.md": "#", "defs/commands/c.md": "---\nname: c\ndescription: d\n---\n",
            "docs/_Sidebar.md": "#", "skills/s/SKILL.md": "#",
        })
        repo = doctidy.analyze(root, self.ruleset, doctidy.ScanOptions())
        classes = {rel: doc.cls for rel, doc in repo.docs.items()}
        expected = {
            "README.md": "entry", "CLAUDE.md": "agent-config", "CHANGELOG.md": "generated",
            ".claude/agents/reviewer.md": "agent-config", ".github/ISSUE_TEMPLATE/bug_report.md": "platform",
            "tests/fixtures/sample.md": "fixture", "_deprecated/old-tool/README.md": "archived",
            "releases/v1.md": "generated", "notes.md": "guide", "plugins/p/commands-ko/run.md": "agent-config",
            "plugins/p/README.md": "entry", "cfg/agents/a.md": "agent-config", "cfg/plans/p.md": "agent-config",
            "cfg/projects/x/memory/n.md": "agent-config", "defs/commands/c.md": "agent-config",
            "docs/_Sidebar.md": "entry", "skills/s/SKILL.md": "agent-config",
        }
        for rel, cls in expected.items():
            with self.subTest(rel=rel):
                self.assertEqual(classes[rel], cls)
        self.assertEqual(repo.docs["docs/agent-only.md"].reach, "agent-only")
        self.assertEqual(repo.docs["_deprecated/old-tool/README.md"].archive, {"root": "_deprecated", "role": "retired"})

    def test_non_git_walk_skips_nested_repositories(self) -> None:
        workspace = self.tmp / "ws"
        self.write(workspace, "loose.md", "# Loose\n")
        self.write(workspace, "node_modules/x/README.md", "#")
        nested = workspace / "inner"
        nested.mkdir()
        (nested / ".git").mkdir()
        self.write(nested, "README.md", "#")
        repo = doctidy.analyze(workspace, self.ruleset, doctidy.ScanOptions())
        self.assertFalse(repo.git)
        self.assertEqual(list(repo.docs), ["loose.md"])
        self.assertEqual(repo.nested_repos, 1)
        self.assertIsNone(repo.docs["loose.md"].tracked)
        self.assertEqual(repo.docs["loose.md"].delete_risk, "no-git")

    def test_missing_path_and_file_argument(self) -> None:
        with self.assertRaises(doctidy.UsageError):
            doctidy.discover(self.tmp / "nope", [])
        code, _, err = run_main(["scan", str(self.tmp / "nope")])
        self.assertEqual(code, doctidy.EXIT_USAGE)
        self.assertIn("path not found", err)

    def test_worktree_fork_detection_and_listing_fallback(self) -> None:
        root = self.repo(files={"README.md": "#"})
        self.git(root, "remote", "add", "upstream", "https://example.com/x.git")
        self.assertTrue(doctidy.is_fork(root))
        worktree = self.tmp / "wt"
        self.git(root, "worktree", "add", "-q", str(worktree))
        self.assertTrue(doctidy.is_fork(worktree))
        with mock.patch.object(doctidy, "run_git", side_effect=lambda root_, *a, **k: b"/x\n" if a[0] == "rev-parse" else None):
            repo = doctidy.discover(self.repo("fallback", {"a.md": "#"}), [])
        self.assertFalse(repo.git)
        self.assertIn("walked the directory", repo.warnings[0])


# ---------------------------------------------------------------------------
# F. Pairs
# ---------------------------------------------------------------------------

class PairTest(Workspace):
    def test_pair_conventions(self) -> None:
        root = self.repo(files={
            "docs/a.md": "# 한국어 문서\n", "docs/a-en.md": "# English\n",
            "docs/b.md": "# B\n", "docs/b-KR.md": "#", "docs/c.md": "#", "docs/c_kr.md": "#",
            "docs/d.md": "#", "docs/d.ja.md": "#", "docs/scaling.md": "#", "docs/scaling-ha.md": "#",
            "docs/lonely-ko.md": "#", "EN_Guide.md": "#", "en/x.md": "#",
            "docs/e.md": "#", "docs/e-zh-cn.md": "#",
            "docs/f.md": "#", "docs/f-xy.md": "#", "docs/g.md": "#", "docs/g-xy.md": "#",
        })
        repo = doctidy.analyze(root, self.ruleset, doctidy.ScanOptions())
        self.assertEqual(repo.docs["docs/a-en.md"].pair, {"lang": "en", "sep": "-", "role": "mirror", "partners": ["docs/a.md"]})
        self.assertEqual(repo.docs["docs/a.md"].pair["partners"], ["docs/a-en.md"])
        for mirror, base in (("docs/b-KR.md", "docs/b.md"), ("docs/c_kr.md", "docs/c.md"), ("docs/d.ja.md", "docs/d.md"),
                             ("docs/e-zh-cn.md", "docs/e.md"), ("docs/f-xy.md", "docs/f.md")):
            with self.subTest(mirror=mirror):
                self.assertEqual(repo.unit_of(mirror), base)
        self.assertNotIn("docs/scaling-ha.md", repo.mirrors)
        self.assertEqual(repo.pair_info["orphanMirrors"], ["docs/lonely-ko.md"])
        self.assertEqual(repo.pair_info["langPrefixFiles"], 1)
        self.assertEqual(repo.pair_info["langDirs"], ["en"])
        conventions = {(c["sep"], c["lang"]): c["baseLang"] for c in repo.pair_info["conventions"]}
        self.assertEqual(conventions[("-", "en")], "ko")
        self.assertEqual(repo.unit_paths("docs/a-en.md"), ["docs/a.md", "docs/a-en.md"])


# ---------------------------------------------------------------------------
# G. Reach
# ---------------------------------------------------------------------------

class ReachTest(Workspace):
    def test_orphan_partner_and_mention(self) -> None:
        root = self.repo(files={
            "README.md": "[guide](docs/guide.md)\nSee `docs/mentioned.md`.\n",
            "docs/guide.md": "# Guide\n[English](guide-en.md)\n",
            "docs/guide-en.md": "# Guide EN\n[Korean](guide.md)\n",
            "docs/orphan.md": "# Orphan\n", "docs/orphan-en.md": "# Orphan EN\n",
            "docs/mentioned.md": "# Mentioned\n",
            "_deprecated/old.md": "[x](../docs/archive-only.md)\n", "docs/archive-only.md": "#",
        })
        repo, findings, _ = self.scan(root)
        self.assertEqual(repo.docs["docs/guide.md"].reach, "linked")
        self.assertEqual(repo.docs["docs/orphan.md"].reach, "orphan")
        self.assertEqual(repo.docs["docs/mentioned.md"].reach, "mention-only")
        self.assertEqual(repo.docs["docs/archive-only.md"].reach, "archive-only")
        orphans = self.codes(findings, "orphan")
        self.assertEqual([(f["path"], f["paths"]) for f in orphans], [("docs/orphan.md", ["docs/orphan.md", "docs/orphan-en.md"])])
        self.assertEqual(self.codes(findings, "mention-only")[0]["evidence"]["items"][0]["path"], "docs/mentioned.md")
        pair_half = self.codes(findings, "unreachable-pair-half")
        self.assertEqual([f["path"] for f in pair_half], ["docs/guide-en.md"])

    def test_wrong_language_link_fix(self) -> None:
        root = self.repo(files={
            "README.md": "[admin](docs/admin-guide.md)\n",
            "README-en.md": "[admin](docs/admin-guide.md#setup) and [admin](docs/admin-guide.md#setup)\n",
            "docs/admin-guide.md": "# 관리자 가이드\n", "docs/admin-guide-en.md": "# Admin guide\n",
            "docs/x.md": "# X\n[en](other-en.md)\n", "docs/x-en.md": "# X EN\n",
            "docs/other.md": "#", "docs/other-en.md": "#",
        })
        repo, findings, _ = self.scan(root)
        wrong = {f["path"]: f for f in self.codes(findings, "wrong-language-link")}
        fix = wrong["README-en.md"]["fix"]
        self.assertEqual(len(fix), 1)
        self.assertEqual((fix[0]["old"], fix[0]["new"], fix[0]["count"], fix[0]["replaceAll"]),
                         ("(docs/admin-guide.md#setup)", "(docs/admin-guide-en.md#setup)", 2, True))
        self.assertTrue(fix[0]["anchorNeedsReview"])
        self.assertEqual(wrong["docs/x.md"]["fix"][0]["new"], "(other.md)")
        self.assertIn("docs/admin-guide-en.md", [f["path"] for f in self.codes(findings, "unreachable-pair-half")])

    def test_agent_only_grouped_by_nearest_readme_and_unindexed_dir(self) -> None:
        root = self.repo(files={
            "README.md": "#", "CLAUDE.md": "`notes/a.md` `notes/b.md`\n", "notes/README.md": "#",
            "notes/a.md": "#", "notes/b.md": "#",
            "pile/one.md": "#", "pile/two.md": "#", "pile/three.md": "#", "pile/linked.md": "#",
            "index-links.md": "[l](pile/linked.md)\n",
        })
        _, findings, _ = self.scan(root)
        agent = self.codes(findings, "agent-only-reachable")
        self.assertEqual([(f["path"], f["paths"]) for f in agent], [("notes/README.md", ["notes/a.md", "notes/b.md"])])
        unindexed = self.codes(findings, "unindexed-dir")
        self.assertEqual(unindexed[0]["path"], "pile")
        self.assertEqual(unindexed[0]["evidence"]["orphans"], ["pile/one.md", "pile/three.md", "pile/two.md"])

    def test_site_orphans_are_suggestions_and_permalinks_exempt(self) -> None:
        root = self.repo(files={"_config.yml": "x", "index.md": "#", "about.md": "---\npermalink: /about/\n---\n",
                                "lost.md": "# Lost\n"})
        repo, findings, _ = self.scan(root)
        self.assertEqual(repo.docs["about.md"].reach, "exempt")
        self.assertEqual([(f["path"], f["severity"]) for f in self.codes(findings, "orphan")], [("lost.md", "suggestion")])


# ---------------------------------------------------------------------------
# H. Class findings
# ---------------------------------------------------------------------------

class ClassFindingTest(Workspace):
    def test_byproducts_keep_marker_and_untracked_weak_signal(self) -> None:
        root = self.repo(files={"README.md": "[p](docs/deploy-followup-prompt.md)\n",
                                "docs/deploy-followup-prompt.md": "# Prompt\n",
                                "docs/kept-handoff.md": "<!-- doc-tidy: keep -->\n# Kept\n"},
                         untracked={"docs/README.old.md": "# Old copy\n"})
        repo, findings, _ = self.scan(root)
        byproducts = {f["path"]: f for f in self.codes(findings, "byproduct")}
        self.assertEqual(set(byproducts), {"docs/deploy-followup-prompt.md", "docs/README.old.md"})
        self.assertEqual(byproducts["docs/README.old.md"]["deleteRisk"], "untracked")
        self.assertEqual(byproducts["docs/deploy-followup-prompt.md"]["evidence"]["inbound"]["human"], 1)
        self.assertEqual(repo.docs["docs/kept-handoff.md"].reach, "exempt")

    def test_runbook_named_after_incidents_is_a_guide(self) -> None:
        root = self.repo(files={"README.md": "[r](incident-response-runbook.md)\n", "incident-response-runbook.md": "# Runbook\n"})
        repo, findings, _ = self.scan(root)
        self.assertEqual(repo.docs["incident-response-runbook.md"].cls, "guide")
        self.assertEqual(self.codes(findings, "historical-record"), [])

    def test_dates_conventions(self) -> None:
        root = self.repo(files={
            "_posts/2026-01-01-hello.md": "# Hello\n",
            "journal/2026-01-01.md": "#", "journal/2026-01-02.md": "#", "journal/2026-01-03.md": "#", "journal/readme-x.md": "#",
            "docs/alarm-incident-2026-04-23.md": "# Alarm\n", "docs/notes-2026-05.md": "# Notes\n",
            "docs/report.md": "# Phase 4 완료 보고 (2026-04-14)\n", "docs/setup.md": "#", "docs/usage.md": "#",
        })
        repo, findings, _ = self.scan(root)
        self.assertTrue(repo.docs["_posts/2026-01-01-hello.md"].dated["conventional"])
        self.assertTrue(repo.docs["journal/2026-01-02.md"].dated["conventional"])
        records = {f["path"]: f["severity"] for f in self.codes(findings, "historical-record")}
        self.assertEqual(records, {"docs/alarm-incident-2026-04-23.md": "warning", "docs/notes-2026-05.md": "suggestion",
                                   "docs/report.md": "warning"})
        self.assertEqual(repo.docs["docs/report.md"].dated, {"date": "2026-04-14", "from": "title", "conventional": False})

    def test_migration_folders(self) -> None:
        root = self.repo(files={
            "docs/done-migration/plan.md": "# Plan\n", "docs/done-migration/status.md": "# Status\n종료 선언\n| a | ✅ |\n",
            "docs/done-migration/01-phase1.md": "#",
            "docs/live/plan.md": "#", "docs/live/01-a.md": "#", "docs/live/02-b.md": "# B\n전환 1/3 단계 진행\n",
            "docs/named-cutover/a.md": "#", "docs/named-cutover/b.md": "#",
        })
        repo, findings, _ = self.scan(root)
        folders = {f["path"]: f for f in self.codes(findings, "migration-folder")}
        self.assertEqual(folders["docs/done-migration"]["severity"], "warning")
        self.assertEqual(folders["docs/done-migration"]["evidence"]["statusFile"], "docs/done-migration/status.md")
        self.assertEqual(folders["docs/live"]["severity"], "suggestion")
        self.assertEqual(folders["docs/live"]["evidence"]["status"]["lean"], "open")
        self.assertIn("docs/named-cutover", folders)
        self.assertEqual(self.codes(findings, "no-archive-convention")[0]["evidence"]["reason"], "none")
        self.assertEqual(self.codes(findings, "historical-record"), [])

    def test_archive_conventions(self) -> None:
        cases = {
            "mirror": ({"_deprecated/tools/x/README.md": "#", "tools/y/README.md": "#"}, "_deprecated", "mirror", False),
            "flat": ({"docs/archive/old.md": "#", "docs/a.md": "#"}, "docs/archive", "flat", False),
            "backup-only": ({"_backup/x/README.md": "#", "x/README.md": "#"}, None, None, True),
            "in-place": ({"old-upgrade/notes.md": "#"}, None, None, True),
            "ambiguous": ({"_deprecated/a.md": "#", "archive/b.md": "#"}, None, None, True),
        }
        for name, (files, preferred, layout, ask) in cases.items():
            with self.subTest(case=name):
                repo = doctidy.analyze(self.repo(name, files), self.ruleset, doctidy.ScanOptions())
                self.assertEqual((repo.archive["preferred"], repo.archive["layout"], repo.archive["needsAsk"]),
                                 (preferred, layout, ask))
        repo = doctidy.analyze(self.repo("targets", {"_deprecated/tools/x.md": "#", "tools/y.md": "#"}),
                               self.ruleset, doctidy.ScanOptions())
        self.assertEqual(doctidy.archive_target(repo.archive, "docs/x.md"), "_deprecated/docs/x.md")
        self.assertEqual(doctidy.archive_target({"preferred": "docs/_archive", "layout": "mirror"}, "docs/a/b.md"),
                         "docs/_archive/a/b.md")
        self.assertEqual(doctidy.archive_target({"preferred": "docs/archive", "layout": "flat"}, "docs/a/b.md"),
                         "docs/archive/b.md")
        self.assertIsNone(doctidy.archive_target({"preferred": None}, "x.md"))
        self.assertEqual(repo.archive["dirs"][0]["rule"], "archive-retired")

    def test_broken_links_and_clusters(self) -> None:
        files = {"README.md": "[gone](gone.md)\n", "_deprecated/a.md": "[x](missing.md)\n"}
        files.update({f"vendored/doc{i}.md": "".join(f"[l](missing{j}.md)\n" for j in range(3)) for i in range(4)})
        _, findings, _ = self.scan(self.repo(files=files))
        broken = {f["path"]: f["severity"] for f in self.codes(findings, "broken-link")}
        self.assertEqual(broken, {"README.md": "critical", "_deprecated/a.md": "suggestion"})
        cluster = self.codes(findings, "broken-link-cluster")[0]
        self.assertEqual((cluster["path"], cluster["evidence"]["broken"]), ("vendored", 12))

    def test_list_findings(self) -> None:
        big = "# Big\n\n## One\n" + ("x" * 2048) + "\n## Two\n"
        root = self.repo(files={"README.md": "#", "CONTRIBUTORS.md": "#", "INVENTORY-ko.md": "#", "notes.md": "#",
                                "docs/big.md": big, "docs/big-en.md": big},
                         untracked={"docs/draft.md": "# Draft\n"})
        os.utime(root / "docs/draft.md", (1788048000, 1788048000))  # 2026-08-30
        _, findings, _ = self.scan(root, split_kb=1)
        clutter = self.codes(findings, "root-clutter")[0]["evidence"]["items"]
        self.assertEqual([item["path"] for item in clutter], ["notes.md"])
        oversize = self.codes(findings, "oversize")[0]["evidence"]["items"]
        self.assertEqual([(i["path"], len(i["mirrorBytes"]), [s["text"] for s in i["sections"]]) for i in oversize],
                         [("docs/big.md", 1, ["One", "Two"])])
        untracked = self.codes(findings, "untracked")[0]["evidence"]["items"]
        self.assertEqual(untracked[0]["path"], "docs/draft.md")
        self.assertGreaterEqual(untracked[0]["ageDays"], 17)

    def test_status_signal_leans(self) -> None:
        cases = {
            "mixed-uncommitted": ("✅ 완료 (미커밋)\n| a | ✅ |\n", "mixed"),
            "mixed-progress": ("| a | ✅ |\n| b | ✅ |\nB1 — 전환 1/3 단계 진행\n", "mixed"),
            "done": ("종료 선언\n| a | ✅ |\n| --- | --- |\n", "done"),
            "open-weak": ("TODO: finish\n", "open"),
            "none": ("plain text\n", "none"),
            "open-table": ("| a | 🔄 |\n| b | 🔄 |\n| c | ✅ |\n| d | ❌ |\n", "open"),
        }
        for name, (text, lean) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(doctidy.status_signals(self.ruleset, doctidy.parse_markdown(text))["lean"], lean)
        signals = doctidy.status_signals(self.ruleset, doctidy.parse_markdown(cases["open-table"][0]))
        self.assertEqual(signals["table"], {"done": 1, "open": 2, "dropped": 1})
        self.assertEqual(signals["evidence"][0]["signal"], "row-open")


# ---------------------------------------------------------------------------
# O. CLI
# ---------------------------------------------------------------------------

class CliTest(Workspace):
    def test_scan_json_text_and_strict(self) -> None:
        root = self.repo(files={"README.md": "[gone](gone.md)\n"}, untracked={"leftover-handoff.md": "# H\n"})
        code, out, _ = run_main(["scan", str(root), "--json", "--now", NOW])
        report = json.loads(out)
        self.assertEqual((code, report["schemaVersion"], report["command"]), (0, 1, "scan"))
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual({d["path"] for d in report["docs"]}, {"README.md", "leftover-handoff.md"})
        code, out, _ = run_main(["scan", str(root), "--json", "--pretty", "--docs", "full"])
        self.assertGreater(out.count("\n"), 10)
        self.assertIn("links", json.loads(out)["docs"][0])
        _, out, _ = run_main(["scan", str(root), "--json", "--docs", "none"])
        self.assertEqual(json.loads(out)["docs"], [])
        code, out, _ = run_main(["scan", str(root), "--strict"])
        self.assertEqual(code, doctidy.EXIT_FINDINGS)
        self.assertIn("🔴 broken-link · README.md", out)
        self.assertIn("[UNTRACKED — delete is permanent]", out)
        self.assertIn("L1: gone.md", out)

    def test_focus_keeps_repo_wide_inbound(self) -> None:
        root = self.repo(files={"README.md": "[a](docs/a.md)\n", "docs/a.md": "#", "docs/b.md": "#", "other/c.md": "#"})
        code, out, _ = run_main(["scan", str(root / "docs"), "--json"])
        report = json.loads(out)
        self.assertEqual(report["focus"], "docs")
        self.assertEqual([f["path"] for f in report["findings"] if f["code"] == "orphan"], ["docs/b.md"])

    def test_run_directory_and_baseline(self) -> None:
        root = self.repo(files={"README.md": "[a](docs/a.md)\n", "docs/a.md": "# A\n", "docs/a-en.md": "# A\n",
                                "docs/leftover-handoff.md": "#"})
        run = self.tmp / "run"
        code, _, _ = run_main(["scan", str(root), "--out", str(run), "--json"])
        self.assertEqual(code, 0)
        batch = json.loads((run / "batch-01.json").read_text(encoding="utf-8"))
        self.assertEqual(batch["of"], 1)
        unit = next(u for u in batch["units"] if u["id"] == "docs/a.md")
        self.assertEqual(unit["paths"], ["docs/a.md", "docs/a-en.md"])
        self.assertEqual(len(unit["docs"]), 2)
        code, _, err = run_main(["scan", str(root), "--out", str(root / "run")])
        self.assertEqual(code, doctidy.EXIT_USAGE)
        self.assertIn("outside the scanned tree", err)

        (root / "docs/a.md").rename(root / "docs/moved.md")
        code, out, _ = run_main(["scan", str(root), "--json", "--baseline", str(run / "scan.json")])
        delta = json.loads(out)["delta"]
        self.assertEqual(code, doctidy.EXIT_FINDINGS)
        self.assertEqual([b["resolved"] for b in delta["newBroken"]], ["docs/a.md"])
        self.assertEqual(delta["splitPairs"], ["docs/a-en.md"])
        _, text, _ = run_main(["scan", str(root), "--baseline", str(run / "scan.json")])
        self.assertIn("since baseline: 1 new broken link(s)", text)
        code, _, err = run_main(["scan", str(root), "--baseline", str(self.tmp / "missing.json")])
        self.assertEqual(code, doctidy.EXIT_USAGE)

    def test_batches_group_folders_and_scope_list_findings(self) -> None:
        big = "# Big\n\n## Part\n" + ("x" * 2048) + "\n"
        root = self.repo(files={
            "README.md": "[s](docs/done-migration/status.md) [g](docs/big.md)\n",
            "docs/done-migration/plan.md": "#", "docs/done-migration/status.md": "# Status\n종료 선언\n",
            "docs/done-migration/01-phase1.md": "#", "docs/big.md": big, "docs/other-big.md": big + "y\n",
        })
        run = self.tmp / "run"
        self.assertEqual(run_main(["scan", str(root), "--out", str(run), "--split-kb", "1", "--json"])[0], 0)
        batch = json.loads((run / "batch-01.json").read_text(encoding="utf-8"))
        units = {unit["id"]: unit for unit in batch["units"]}
        folder = units["docs/done-migration"]
        self.assertEqual((folder["kind"], len(folder["paths"]), len(folder["docs"])), ("folder", 3, 3))
        self.assertNotIn("docs/done-migration/status.md", units)
        big_unit = units["docs/big.md"]
        oversize = next(f for f in big_unit["findings"] if f["code"] == "oversize")
        self.assertEqual([item["path"] for item in oversize["evidence"]["items"]], ["docs/big.md"])
        record = big_unit["docs"][0]
        self.assertEqual(record["inbound"]["from"], ["README.md:1 human"])
        self.assertIn("sections", record)
        self.assertNotIn("partners", record)

    def test_usage_errors(self) -> None:
        code, _, err = run_main([])
        self.assertEqual(code, doctidy.EXIT_USAGE)
        self.assertIn("choose a command", err)
        with self.assertRaises(SystemExit):
            run_main(["scan", "--now", "not-a-date"])

    def test_text_report_truncates_and_names_archive(self) -> None:
        files = {"README.md": "#", "_deprecated/tools/a.md": "#", "tools/b/README.md": "#"}
        files.update({f"d{i}/orphan.md": "#" for i in range(22)})
        root = self.repo(files=files)
        _, out, _ = run_main(["scan", str(root)])
        self.assertIn("archive: _deprecated (mirror)", out)
        self.assertIn("… 2 more orphan", out)
        files = {"_deprecated/a.md": "#", "archive/b.md": "#"}
        _, out, _ = run_main(["scan", str(self.repo("amb", files))])
        self.assertIn("archive: needs a decision (ambiguous)", out)


# ---------------------------------------------------------------------------
# I. History and drift
# ---------------------------------------------------------------------------

class HistoryTest(Workspace):
    def test_drift_measures_from_the_component_directory(self) -> None:
        root = self.repo(files={"svc/docs/troubleshooting.md": "# T\n", "svc/values.yaml": "a: 1\n", "docs/top.md": "#",
                                "README.md": "#", "svc/README.md": "# Svc\n"}, when="2026-04-06T10:00:00")
        self.write(root, "svc/values.yaml", "a: 2\n")
        self.write(root, "README.md", "# changed root\n")
        self.write(root, "docs/diagram.png", "png")
        self.commit(root, "2026-09-08T10:00:00")
        repo, findings, report = self.scan(root)
        drift = repo.docs["svc/docs/troubleshooting.md"].drift
        self.assertEqual((drift["describes"], drift["days"], drift["codeCommitsSince"]), ("svc", 155, 1))
        self.assertEqual(drift["docLast"], "2026-04-06")
        self.assertIsNone(repo.docs["docs/top.md"].drift)
        self.assertIsNone(repo.docs["README.md"].drift)
        self.assertEqual(repo.docs["svc/README.md"].drift["describes"], "svc")
        items = self.codes(findings, "drift")[0]["evidence"]["items"]
        self.assertEqual([i["path"] for i in items][:1], ["svc/README.md"])
        self.assertEqual(report["history"]["commits"], 2)
        self.assertEqual(repo.docs["svc/README.md"].git["commits"], 1)

    def test_asset_changes_are_not_code(self) -> None:
        root = self.repo(files={"svc/docs/guide.md": "#", "svc/docs/diagram.png": "a"}, when="2026-01-01T00:00:00")
        self.write(root, "svc/docs/diagram.png", "b")
        self.commit(root, "2026-09-01T00:00:00")
        repo, _, _ = self.scan(root)
        self.assertIsNone(repo.docs["svc/docs/guide.md"].drift)

    def test_bulk_commits_do_not_refresh_documents(self) -> None:
        root = self.repo(files={"svc/docs/guide.md": "#", "svc/main.py": "x"}, when="2026-01-01T00:00:00")
        self.write(root, "svc/main.py", "y")
        self.commit(root, "2026-06-01T00:00:00")
        for i in range(20):
            self.write(root, f"other/doc{i}.md", "#")
        self.write(root, "svc/docs/guide.md", "# reworded\n")
        self.commit(root, "2026-09-01T00:00:00")
        repo, _, report = self.scan(root, bulk_md=20)
        git = repo.docs["svc/docs/guide.md"].git
        self.assertEqual((git["last"], git["lastSubstantive"]), ("2026-09-01", "2026-01-01"))
        self.assertEqual(repo.docs["svc/docs/guide.md"].drift["days"], 151)
        self.assertEqual(report["history"]["bulkCommits"], 1)

    def test_history_unavailable(self) -> None:
        root = self.repo(files={"a/docs/x.md": "#"})
        _, _, report = self.scan(root, history=False)
        self.assertEqual(report["history"]["reason"], "disabled")
        plain = self.tmp / "plain"
        self.write(plain, "x.md", "#")
        _, _, report = self.scan(plain)
        self.assertEqual(report["history"]["reason"], "not a git repository")
        repo = doctidy.discover(root, [])
        repo.shallow = True
        doctidy.load_history(repo, doctidy.ScanOptions())
        self.assertIn("shallow clone", repo.warnings[0])
        original = doctidy.run_git
        with mock.patch.object(doctidy, "run_git", side_effect=lambda r, *a, **k: None if a[0] == "log" else original(r, *a, **k)):
            repo = doctidy.analyze(root, self.ruleset, doctidy.ScanOptions())
        self.assertIn("git log failed", repo.warnings[0])
        self.assertEqual(doctidy.compute_drift(repo.docs["a/docs/x.md"], {"last": 0, "lastSubstantive": None},
                                               {"a": [86400 * 10]}, 60), None)


# ---------------------------------------------------------------------------
# J. Similarity
# ---------------------------------------------------------------------------

def prose(seed: str, words: int = 80) -> str:
    return "# Doc\n\n" + " ".join(f"{seed}{i}" for i in range(words)) + "\n"


class SimilarityTest(Workspace):
    def test_duplicates_overlaps_and_archive_copies(self) -> None:
        base = prose("alpha")
        files = {
            "README.md": "#", "docs/copy1.md": base, "docs/copy2.md": base,
            "docs/near.md": prose("beta"), "docs/near2.md": prose("beta") + "tail words here now\n",
            "docs/half.md": prose("gamma", 80), "docs/half2.md": prose("gamma", 60) + " ".join(f"delta{i}" for i in range(20)),
            "docs/other.md": prose("epsilon"), "tools/tool.md": prose("zeta"), "_deprecated/tools/tool.md": prose("zeta"),
            "_deprecated/a.md": prose("eta"), "_deprecated/b.md": prose("eta"),
            "docs/pair.md": prose("theta"), "docs/pair-en.md": prose("theta"), "docs/tiny.md": "# t\none two\n",
            "docs/tiny2.md": "# t\none two\n",
        }
        _, findings, _ = self.scan(self.repo(files=files))
        duplicates = sorted((f["path"], tuple(f["paths"]), f["evidence"]["identical"]) for f in self.codes(findings, "duplicate"))
        self.assertEqual(duplicates, [("docs/copy1.md", ("docs/copy1.md", "docs/copy2.md"), True),
                                      ("docs/near.md", ("docs/near.md", "docs/near2.md"), False)])
        overlap = self.codes(findings, "overlap")[0]["evidence"]["items"]
        self.assertEqual([item["paths"] for item in overlap], [["docs/half.md", "docs/half2.md"]])
        copies = [(f["evidence"]["active"], f["evidence"]["archived"]) for f in self.codes(findings, "archive-copy")]
        self.assertEqual(copies, [("tools/tool.md", "_deprecated/tools/tool.md")])

    def test_many_identical_copies_and_near_archive_copy(self) -> None:
        files = {f"templates/t{i:02d}.md": prose("same") for i in range(12)}
        files.update({"live.md": prose("iota") + "extra words one two\n", "_deprecated/live.md": prose("iota")})
        files.update({"solo.md": prose("kappa"), "_deprecated/solo.md": prose("kappa"), "_deprecated/solo2.md": prose("kappa")})
        _, findings, _ = self.scan(self.repo(files=files))
        group = self.codes(findings, "duplicate")[0]
        self.assertEqual(len(group["paths"]), 12)
        copies = sorted((f["evidence"]["active"], f["evidence"]["archived"]) for f in self.codes(findings, "archive-copy"))
        self.assertEqual(copies, [("live.md", "_deprecated/live.md"), ("solo.md", "_deprecated/solo.md"),
                                  ("solo.md", "_deprecated/solo2.md")])

    def test_similarity_limits(self) -> None:
        root = self.repo(files={"a.md": prose("x"), "b.md": prose("x")})
        with mock.patch.object(doctidy, "MAX_SIMILARITY_DOCS", 1):
            repo, findings, _ = self.scan(root)
        self.assertEqual(self.codes(findings, "duplicate"), [])
        self.assertIn("similarity skipped", repo.warnings[0])
        _, findings, _ = self.scan(root, similarity=False)
        self.assertEqual(self.codes(findings, "duplicate"), [])


# ---------------------------------------------------------------------------
# K. Plans
# ---------------------------------------------------------------------------

class PlansTest(Workspace):
    def config(self, settings: dict | str | None = None) -> Path:
        config = self.tmp / "claude"
        (config / "plans" / "templates").mkdir(parents=True)
        if settings is not None:
            body = settings if isinstance(settings, str) else json.dumps(settings)
            (config / "settings.json").write_text(body, encoding="utf-8")
        return config

    def plan(self, config: Path, name: str, text: str, age_days: int) -> Path:
        path = config / "plans" / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        when = (doctidy.dt.datetime.combine(doctidy.parse_date(NOW), doctidy.dt.time(12)) -
                doctidy.dt.timedelta(days=age_days)).timestamp()
        os.utime(path, (when, when))
        return path

    def run_plans(self, *extra: str) -> tuple[int, dict]:
        code, out, err = run_main(["plans", "--json", "--now", NOW, *extra])
        self.assertIn(code, (0, 1), err)
        return code, json.loads(out)

    def test_expiry_references_and_dangling(self) -> None:
        config = self.config({"cleanupPeriodDays": 10})
        self.plan(config, "brisk-folding-lamport", "# Aging plan\n\n## Lessons learned\n종료 선언\n", age_days=9)
        self.plan(config, "calm-reading-knuth", "# Fresh\nSee brisk-folding-lamport and ~/.claude/plans/brisk-folding-lamport.md\n", 1)
        (config / "CLAUDE.md").write_text(
            "Catalog: `~/.claude/plans/gone-catalog-plan.md`\nAlso /home/you/.claude/plans/gone-catalog-plan.md\n"
            "Current: ~/.claude/plans/brisk-folding-lamport.md\nExample: plans/foo.md and `plans/<name>.md`\n"
            "```\n$CLAUDE_CONFIG_DIR/plans/gone-in-fence.md\n```\n", encoding="utf-8")
        (config / "plans" / "templates" / "t.md").write_text("Catalog W1 in `~/.claude/plans/gone-catalog-plan.md`\n", encoding="utf-8")
        memory = config / "projects" / "-x" / "memory"
        memory.mkdir(parents=True)
        (memory / "note.md").write_text("see ~/.claude/plans/gone-memory-plan.md\n", encoding="utf-8")
        (config / "projects" / "-x" / "session.jsonl").write_text('"~/.claude/plans/in-transcript.md"\n', encoding="utf-8")
        (config / "skills" / "synced" / "x").mkdir(parents=True)
        (config / "skills" / "synced" / "x" / "SKILL.md").write_text("~/.claude/plans/synced-plan.md\n", encoding="utf-8")
        (config / ".last-cleanup").write_text("2026-09-17T03:15:48Z\n", encoding="utf-8")

        code, report = self.run_plans("--config-dir", str(config), "--strict")
        self.assertEqual(code, doctidy.EXIT_FINDINGS)
        self.assertEqual((report["retention"]["days"], report["retention"]["source"]), (10, str(config / "settings.json")))
        plans = {p["name"]: p for p in report["plans"]}
        self.assertEqual(plans["brisk-folding-lamport"]["daysLeft"], 1)
        self.assertEqual(plans["brisk-folding-lamport"]["refs"]["durable"], 1)
        self.assertEqual(plans["brisk-folding-lamport"]["refs"]["fromPlans"], 2)
        self.assertEqual(plans["brisk-folding-lamport"]["sections"], [{"line": 3, "text": "Lessons learned"}])
        self.assertEqual(plans["brisk-folding-lamport"]["status"]["lean"], "done")
        dangling = {d["slug"]: d for d in report["dangling"]}
        self.assertEqual(sorted(dangling), ["gone-catalog-plan", "gone-in-fence", "gone-memory-plan"])
        self.assertEqual(len(dangling["gone-catalog-plan"]["refs"]), 3)
        self.assertEqual({r["source"] for r in dangling["gone-catalog-plan"]["refs"]}, {"config", "template"})
        self.assertTrue(dangling["gone-in-fence"]["refs"][0]["inFence"])
        self.assertEqual(dangling["gone-memory-plan"]["refs"][0]["source"], "memory")
        self.assertEqual(report["refsScanned"]["placeholders"], 1)
        self.assertEqual(report["templates"], [{"path": str(config / "plans/templates/t.md"), "refsOut": 1}])
        self.assertEqual(report["lastHousekeeping"], "2026-09-17T03:15:48Z")
        expiring = [f for f in report["findings"] if f["code"] == "plan-expiring"]
        self.assertEqual([(f["path"].endswith("brisk-folding-lamport.md"), f["severity"]) for f in expiring], [(True, "critical")])
        _, text, _ = run_main(["plans", "--config-dir", str(config), "--now", NOW])
        self.assertIn("🔴 dangling-plan-ref", text)
        self.assertIn("retention 10 days", text)

    def test_retention_sources(self) -> None:
        config = self.config("{not json")
        project = self.tmp / "project"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 7, "plansDirectory": "notes/plans"}), encoding="utf-8")
        (project / ".claude" / "settings.local.json").write_text(json.dumps({"plansDirectory": "../elsewhere", "cleanupPeriodDays": "x"}), encoding="utf-8")
        self.plan(config, "p", "# P\n", age_days=3)
        with mock.patch.dict(doctidy.MANAGED_SETTINGS, {sys.platform: self.tmp / "no-managed.json"}):
            _, report = self.run_plans("--config-dir", str(config), "--project", str(project))
        retention = report["retention"]
        self.assertEqual((retention["days"], retention["source"]), (7, str(project / ".claude/settings.json")))
        errors = {Path(s["file"]).name: s["error"] for s in retention["sources"]}
        self.assertIn("cannot parse", errors["settings.json" if False else "settings.json"] or errors.get("settings.json", "cannot parse"))
        self.assertTrue(any("not an integer" in (s["error"] or "") for s in retention["sources"]))
        custom = {c["setting"]: c["withinProject"] for c in report["customPlansDirs"]}
        self.assertEqual(custom, {"notes/plans": True, "../elsewhere": False})
        _, report = self.run_plans("--config-dir", str(config), "--retention-days", "0")
        self.assertTrue(report["retention"]["disabled"])
        self.assertIsNone(report["plans"][0]["daysLeft"])
        self.assertEqual(report["findings"], [])
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(config)}):
            _, report = self.run_plans()
        self.assertEqual(report["configDir"], str(config))
        _, report = self.run_plans("--config-dir", str(self.tmp / "missing"))
        self.assertIn("config directory not found", report["warnings"][0])
        self.assertEqual(report["retention"]["days"], 30)

    def test_recover_refs_roots_and_workspaces(self) -> None:
        config = self.config()
        (config / "CLAUDE.md").write_text("~/.claude/plans/lost-plan.md\n", encoding="utf-8")
        mirror = self.repo("mirror", {"plans/lost-plan.md": "# Lost\n", "plans/deep/x.md": "#"}, when="2026-06-01T00:00:00")
        self.git(mirror, "rm", "-q", "plans/lost-plan.md", "plans/deep/x.md")
        self.commit(mirror, "2026-07-01T00:00:00")
        work = self.tmp / "work"
        repo_a = self.repo("work/app-repo", {"CLAUDE.md": "old plan: ~/.claude/plans/repo-plan.md\n",
                                             ".claude/agents/a.md": "~/.claude/plans/agent-plan.md\n"})
        fork = self.repo("work/forked", {"CLAUDE.md": "~/.claude/plans/fork-plan.md\n"})
        self.git(fork, "remote", "add", "upstream", "https://example.com/u.git")
        self.write(work, "handoff-notes.md", "# Handoff\nwork in /app-repo/docs and app-repo too\n")
        self.write(work, "notes-dir/a.md", "#")
        self.write(work, ".hidden/b.md", "#")
        code, report = self.run_plans("--config-dir", str(config), "--recover-from", f"{mirror}:plans",
                                      "--refs-root", str(work), "--refs-root", str(repo_a), "--workspace", str(work))
        dangling = {d["slug"]: d for d in report["dangling"]}
        self.assertEqual(sorted(dangling), ["agent-plan", "lost-plan", "repo-plan"])
        recoverable = dangling["lost-plan"]["recoverable"]
        self.assertEqual(recoverable["show"], f"{recoverable['commit']}^:plans/lost-plan.md")
        self.assertEqual(recoverable["date"], "2026-07-01")
        self.assertIsNone(dangling["repo-plan"]["recoverable"])
        workspace = report["workspaces"][0]
        loose = workspace["looseDocs"][0]
        self.assertEqual(loose["mentionsRepos"][0], {"repo": "app-repo", "pathHits": 1, "tokenHits": 2})
        self.assertEqual(workspace["nonGitDirs"], [{"path": str(work / "notes-dir"), "docs": 1}])
        self.assertEqual([f["code"] for f in report["findings"] if f["path"] == str(work / "handoff-notes.md")], ["loose-doc"])
        _, report = self.run_plans("--config-dir", str(config), "--recover-from", str(mirror))
        self.assertIsNotNone(report["dangling"][0]["recoverable"])

    def test_plan_referenced_far_from_expiry(self) -> None:
        config = self.config()
        self.plan(config, "steady-plan", "# Steady\n", age_days=1)
        (config / "agents").mkdir()
        (config / "agents" / "a.md").write_text("see ~/.claude/plans/steady-plan.md\n", encoding="utf-8")
        _, report = self.run_plans("--config-dir", str(config))
        self.assertEqual([f["code"] for f in report["findings"]], ["plan-referenced"])


# ---------------------------------------------------------------------------
# M. Sweep
# ---------------------------------------------------------------------------

class SweepTest(Workspace):
    def test_sweep_ranks_and_skips(self) -> None:
        self.repo("root/noisy", {"README.md": "[x](gone.md)\n", "docs/lost.md": "#"})
        self.repo("root/clean", {"README.md": "#"})
        fork = self.repo("root/forked", {"README.md": "[x](gone.md)\n"})
        self.git(fork, "remote", "add", "upstream", "https://example.com/u.git")
        self.repo("root/private-copy", {"README.md": "#"})
        root = self.tmp / "root"
        self.write(root, "loose-handoff.md", "# Loose\n")
        self.write(root, "plain/a.md", "#")
        (root / ".hidden").mkdir()
        (root / "node_modules").mkdir()
        code, out, _ = run_main(["sweep", str(root), "--json", "--exclude", "*-copy", "--now", NOW])
        report = json.loads(out)
        entry = report["roots"][0]
        self.assertEqual([Path(r["path"]).name for r in report["ranking"]], ["noisy", "clean"])
        self.assertEqual(sorted((Path(s["path"]).name, s["reason"]) for s in entry["skipped"]),
                         [(".hidden", "hidden"), ("forked", "fork"), ("node_modules", "skip-dir"), ("private-copy", "exclude")])
        self.assertEqual(entry["nonGitDirs"], [{"path": str(root / "plain"), "docs": 1}])
        self.assertEqual(Path(entry["looseDocs"][0]["path"]).name, "loose-handoff.md")
        _, out, _ = run_main(["sweep", str(root), "--include-forks", "--top", "2", "--now", NOW])
        self.assertIn("forked", out)
        self.assertNotIn("clean", out.split("skipped")[0])
        self.assertIn("🟡 loose-doc", out)
        _, out, _ = run_main(["sweep", str(root / "noisy"), "--json"])
        self.assertEqual(len(json.loads(out)["roots"][0]["repos"]), 1)
        code, _, err = run_main(["sweep", str(root / "missing")])
        self.assertEqual(code, doctidy.EXIT_USAGE)


# ---------------------------------------------------------------------------
# N. relink and apply
# ---------------------------------------------------------------------------

class RelinkTest(Workspace):
    def build(self, **extra: str) -> Path:
        files = {
            "README.md": "[a](docs/a.md#s) and [a](docs/a.md#s)\nSee `docs/a.md`.\n[docs](docs/)\n",
            "README-en.md": "[a](docs/a-en.md)\n",
            "docs/a.md": "# A\n[b](b.md) ![img](img/p.png) [gone](missing.md)\n",
            "docs/a-en.md": "# A EN\n[b](b.md)\n",
            "docs/b.md": "# B\nSee [a](a.md) now\n[root](/docs/a.md)\n\n```md\nExample: [a](a.md)\n```\n",
            "docs/img/p.png": "png",
            "CLAUDE.md": "Read `docs/a.md` first.\n",
            "CHANGELOG.md": "- [a](docs/a.md)\n",
            "Makefile": "lint:\n\tmarkdownlint docs/a.md\n",
            "_deprecated/tools/x.md": "#", "tools/y.md": "#",
        }
        files.update(extra)
        return self.repo(files=files)

    def relink(self, root: Path, *args: str) -> tuple[int, dict]:
        code, out, err = run_main(["relink", str(root), "--json", *args])
        self.assertIn(code, (0, 1), err)
        return code, json.loads(out)

    def test_move_rewrites_every_link_style(self) -> None:
        root = self.build()
        code, report = self.relink(root, "--from", "docs/a.md", "--to", "archive/docs/a.md")
        self.assertEqual(code, 0)
        self.assertEqual([(o["from"], o["to"], o["via"], o["expandedFrom"]) for o in report["operations"]],
                         [("docs/a.md", "archive/docs/a.md", "git-mv", None),
                          ("docs/a-en.md", "archive/docs/a-en.md", "git-mv", "pair")])
        self.assertEqual(report["operations"][0]["mkdirs"], ["archive", "archive/docs"])
        edits = {(e["file"], e["old"]): e for e in report["edits"]}
        self.assertEqual(edits[("README.md", "(docs/a.md#s)")]["new"], "(archive/docs/a.md#s)")
        self.assertEqual((edits[("README.md", "(docs/a.md#s)")]["count"], edits[("README.md", "(docs/a.md#s)")]["replaceAll"]), (2, True))
        self.assertEqual(edits[("README.md", "`docs/a.md`")]["new"], "`archive/docs/a.md`")
        self.assertEqual(edits[("README-en.md", "(docs/a-en.md)")]["new"], "(archive/docs/a-en.md)")
        self.assertEqual(edits[("docs/a.md", "(b.md)")]["new"], "(../../docs/b.md)")
        self.assertEqual(edits[("docs/a.md", "(b.md)")]["fileAfter"], "archive/docs/a.md")
        self.assertEqual(edits[("docs/a.md", "(img/p.png)")]["new"], "(../../docs/img/p.png)")
        self.assertEqual(edits[("docs/a.md", "(missing.md)")]["new"], "(../../docs/missing.md)")
        self.assertEqual(edits[("docs/b.md", "See [a](a.md) now")]["new"], "See [a](../archive/docs/a.md) now")
        self.assertEqual(edits[("docs/b.md", "(/docs/a.md)")]["new"], "(/archive/docs/a.md)")
        self.assertEqual(edits[("CLAUDE.md", "`docs/a.md`")]["new"], "`archive/docs/a.md`")
        self.assertEqual([(t["file"], t["line"]) for t in report["textRefs"]], [("Makefile", 2)])
        self.assertIn(("CHANGELOG.md", "generated-source"), [(w["path"], w["reason"]) for w in report["opWarnings"]])
        _, text, _ = run_main(["relink", str(root), "--from", "docs/a.md", "--to", "archive/docs/a.md"])
        self.assertIn("git-mv docs/a-en.md -> archive/docs/a-en.md  [pair]", text)
        self.assertIn("🟡 text   Makefile:2", text)

    def test_apply_then_verify(self) -> None:
        root = self.build()
        run = self.tmp / "run"
        self.assertEqual(run_main(["scan", str(root), "--out", str(run), "--json"])[0], 0)
        _, report = self.relink(root, "--from", "docs/a.md", "--to", "archive/docs/a.md")
        plan = run / "plan.json"
        plan.write_text(json.dumps(report), encoding="utf-8")
        code, out, _ = run_main(["apply", "--plan", str(plan)])
        self.assertEqual(code, 0, out)
        self.assertIn("doc-tidy apply: done", out)
        status = self.git(root, "status", "--porcelain")
        self.assertIn("docs/a.md -> archive/docs/a.md", status)
        self.assertEqual(status.splitlines()[0][:2], " M")
        self.assertIn("[a](archive/docs/a.md#s) and [a](archive/docs/a.md#s)", (root / "README.md").read_text(encoding="utf-8"))
        self.assertIn("Example: [a](a.md)", (root / "docs/b.md").read_text(encoding="utf-8"))
        code, out, _ = run_main(["scan", str(root), "--json", "--baseline", str(run / "scan.json")])
        delta = json.loads(out)["delta"]
        self.assertEqual((code, delta["newBroken"], delta["splitPairs"]), (0, [], []))
        code, out, _ = run_main(["apply", "--plan", str(plan), "--json", "--allow-staged"])
        result = json.loads(out)
        self.assertEqual((code, result["applied"]), (doctidy.EXIT_FINDINGS, False))
        self.assertTrue(any("no longer exists" in p for p in result["problems"]))

    def test_apply_refuses_without_writing(self) -> None:
        root = self.build()
        _, report = self.relink(root, "--from", "docs/a.md", "--to", "archive/docs/a.md")
        plan = self.tmp / "plan.json"
        plan.write_text(json.dumps(report), encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.write(root, "README.md", readme.replace("[a](docs/a.md#s) and ", ""))
        code, out, _ = run_main(["apply", "--plan", str(plan)])
        self.assertEqual(code, doctidy.EXIT_FINDINGS)
        self.assertIn("nothing changed", out)
        self.assertIn("expected 2", out)
        self.assertTrue((root / "docs/a.md").exists())
        self.assertEqual(self.git(root, "status", "--porcelain").strip(), "M README.md")
        self.write(root, "README.md", readme)
        self.write(root, "staged.txt", "x")
        self.git(root, "add", "staged.txt")
        code, out, _ = run_main(["apply", "--plan", str(plan)])
        self.assertIn("already staged", out)
        self.git(root, "reset", "-q", "staged.txt")
        for mutate, message in (
            (lambda p: {**p, "refusals": [{"x": 1}]}, "refusal"),
            (lambda p: {**p, "manual": [{"x": 1}]}, "manual edit"),
        ):
            bad = self.tmp / "bad.json"
            bad.write_text(json.dumps(mutate(report)), encoding="utf-8")
            self.assertIn(message, run_main(["apply", "--plan", str(bad)])[1])
        for payload, message in (({"tool": "other"}, "relink result"),
                                 ({**report, "root": str(self.tmp)}, "top of a git repository")):
            bad = self.tmp / "bad.json"
            bad.write_text(json.dumps(payload), encoding="utf-8")
            code, _, err = run_main(["apply", "--plan", str(bad)])
            self.assertEqual(code, doctidy.EXIT_USAGE)
            self.assertIn(message, err)
        code, _, err = run_main(["apply", "--plan", str(self.tmp / "absent.json")])
        self.assertEqual(code, doctidy.EXIT_USAGE)

    def test_overlapping_edits_and_unreadable_files_are_refused(self) -> None:
        root = self.build()
        plan = {"tool": "doc-tidy", "command": "relink", "root": str(root), "refusals": [], "manual": [], "operations": [],
                "edits": [{"file": "README.md", "old": "(docs/a.md#s)", "new": "(x)", "count": 2, "replaceAll": True, "lines": [1]},
                          {"file": "README.md", "old": "docs/a.md#s)", "new": "y", "count": 2, "replaceAll": True, "lines": [1]},
                          {"file": "gone.md", "old": "x", "new": "y", "count": 1, "replaceAll": False, "lines": [1]}]}
        path = self.tmp / "p.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        _, out, _ = run_main(["apply", "--plan", str(path)])
        self.assertIn("two edits overlap", out)
        self.assertIn("gone.md: cannot read", out)

    def test_archive_merge_delete_and_directory_moves(self) -> None:
        root = self.build(**{"docs/c.md": "# C\n", "notes.md": "[c](docs/c.md#part) [b](docs/b.md)\n",
                             "guide/README.md": "#", "guide/x.md": "[r](README.md)\n", "links.md": "[g](guide/) [x](guide/x.md)\n"})
        _, report = self.relink(root, "--archive", "docs/a.md")
        self.assertEqual(report["operations"][0]["to"], "_deprecated/docs/a.md")
        _, report = self.relink(root, "--archive", "docs/a.md", "--archive-dir", "docs/old")
        self.assertEqual(report["operations"][0]["to"], "docs/old/a.md")
        _, report = self.relink(root, "--merge", "docs/c.md:docs/b.md")
        merged = next(e for e in report["edits"] if e["file"] == "notes.md")
        self.assertEqual((merged["new"], merged["reason"], merged["anchorNeedsReview"]), ("(docs/b.md#part)", "merged", True))
        self.assertEqual(report["operations"][0], {"op": "delete", "from": "docs/c.md", "to": None, "mergedInto": "docs/b.md",
                                                   "tracked": True, "via": "git-rm", "mkdirs": [], "expandedFrom": None})
        _, report = self.relink(root, "--delete", "docs/b.md")
        self.assertEqual(sorted((b["file"], b["unlinked"]) for b in report["breaks"]),
                         [("docs/a-en.md", "b"), ("docs/a.md", "b"), ("notes.md", "b")])
        _, report = self.relink(root, "--from", "guide", "--to", "handbook")
        self.assertEqual({o["expandedFrom"] for o in report["operations"]}, {"dir"})
        edits = {(e["file"], e["old"]): e["new"] for e in report["edits"]}
        self.assertEqual(edits, {("links.md", "(guide/)"): "(handbook/)", ("links.md", "(guide/x.md)"): "(handbook/x.md)"})
        _, report = self.relink(root, "--from", "guide/README.md", "--to", "handbook/README.md")
        self.assertIn("dir-index-moved", [w["reason"] for w in report["opWarnings"]])
        _, report = self.relink(root, "--delete", "docs", "--no-pair")
        self.assertEqual({o["expandedFrom"] for o in report["operations"]}, {"dir"})
        _, report = self.relink(root, "--from", "docs/a.md", "--to", "moved/", "--no-pair")
        self.assertEqual([o["to"] for o in report["operations"]], ["moved/a.md"])
        _, report = self.relink(root, "--from", str(root / "docs/b.md"), "--to", "tools")
        self.assertEqual(report["operations"][0]["to"], "tools/b.md")

    def test_pair_names_follow_their_source(self) -> None:
        root = self.build(**{"docs/k.md": "#", "docs/k-KR.md": "#", "docs/k-ja.md": "#"})
        _, report = self.relink(root, "--from", "docs/k-KR.md", "--to", "docs/renamed-KR.md")
        self.assertEqual(sorted((o["from"], o["to"]) for o in report["operations"]),
                         [("docs/k-KR.md", "docs/renamed-KR.md"), ("docs/k-ja.md", "docs/renamed-ja.md"),
                          ("docs/k.md", "docs/renamed.md")])
        _, report = self.relink(root, "--merge", "docs/a-en.md:docs/b.md")
        self.assertEqual(sorted((o["from"], o["mergedInto"]) for o in report["operations"]),
                         [("docs/a-en.md", "docs/b.md"), ("docs/a.md", None)])

    def test_refusals(self) -> None:
        root = self.build(**{".gitignore": "ignored/\n", "docs/z.md": "#"})
        (root / "docs/link.md").symlink_to(root / "docs/b.md")
        nested = root / "vendor-repo"
        nested.mkdir()
        (nested / ".git").mkdir()
        cases = [
            (["--from", "docs/b.md", "--to", "tools/y.md"], "target-exists"),
            (["--from", "docs/b.md", "--to", "../escape.md"], "outside-repo"),
            (["--from", "docs/b.md", "--to", "same.md", "--from", "docs/z.md", "--to", "same.md"], "collision"),
            (["--from", "docs/b.md", "--to", "docs/z.md", "--from", "docs/z.md", "--to", "docs/b.md"], "collision"),
            (["--from", "docs", "--to", "docs/inner", "--no-pair"], "into-itself"),
            (["--from", "docs/nope.md", "--to", "x.md"], "missing"),
            (["--from", "docs/b.md", "--to", "ignored/b.md"], "target-ignored"),
            (["--from", "docs/b.md", "--to", "vendor-repo/b.md"], "nested-repo"),
            (["--merge", "docs/b.md:docs/nope.md"], "missing"),
            (["--archive", "docs/b.md", "--archive-dir", "x"], None),
        ]
        for args, reason in cases:
            with self.subTest(args=args):
                code, report = self.relink(root, *args)
                reasons = {r["reason"] for r in report["refusals"]}
                if reason:
                    self.assertIn(reason, reasons)
                    self.assertEqual(code, doctidy.EXIT_FINDINGS)
                else:
                    self.assertEqual(reasons, set())
        repo = doctidy.analyze(root, self.ruleset, doctidy.ScanOptions())
        refusals: list = []
        doctidy.check_operations(repo, [doctidy.Operation("move", "docs/link.md", "x.md")], refusals)
        self.assertEqual(refusals[0]["reason"], "symlink")
        plain = self.repo("no-archive", {"a.md": "#", "b.md": "[a](a.md)\n"})
        code, report = self.relink(plain, "--archive", "a.md")
        self.assertEqual(report["refusals"][0]["reason"], "no-archive-convention")
        ambiguous = self.repo("ambiguous", {"_deprecated/a.md": "#", "archive/b.md": "#", "c.md": "#"})
        _, report = self.relink(ambiguous, "--archive", "c.md")
        self.assertEqual(report["refusals"][0]["reason"], "ambiguous-archive")
        _, text, _ = run_main(["relink", str(plain), "--archive", "a.md"])
        self.assertIn("🔴 refused move a.md", text)

    def test_untracked_case_only_and_manual(self) -> None:
        root = self.build(**{"docs/dup.md": "[a](a.md)\n\n```\n[a](a.md)\n```\n"})
        self.write(root, "docs/draft.md", "# Draft\n[b](b.md)\n")
        _, report = self.relink(root, "--from", "docs/draft.md", "--to", "docs/final/draft.md")
        self.assertEqual(report["operations"][0]["via"], "mv")
        self.assertIn("untracked", [w["reason"] for w in report["opWarnings"]])
        plan = self.tmp / "u.json"
        plan.write_text(json.dumps(report), encoding="utf-8")
        self.assertEqual(run_main(["apply", "--plan", str(plan)])[0], 0)
        self.assertTrue((root / "docs/final/draft.md").exists())
        _, report = self.relink(root, "--delete", "docs/final/draft.md")
        plan.write_text(json.dumps(report), encoding="utf-8")
        self.assertIn("untracked - delete it yourself", run_main(["apply", "--plan", str(plan)])[1])

        self.git(root, "add", "-A")
        self.commit(root, "2026-02-01T00:00:00")
        _, report = self.relink(root, "--from", "docs/b.md", "--to", "docs/B.md", "--no-pair")
        self.assertIn("case-only-rename", [w["reason"] for w in report["opWarnings"]])
        _, report = self.relink(root, "--from", "docs/a.md", "--to", "docs/new-a.md", "--no-pair")
        manual = [(m["file"], m["line"]) for m in report["manual"]]
        self.assertEqual(manual, [("docs/dup.md", 1)])
        _, text, _ = run_main(["relink", str(root), "--from", "docs/a.md", "--to", "docs/new-a.md", "--no-pair"])
        self.assertIn("🟡 manual docs/dup.md:1", text)
        plan.write_text(json.dumps(report), encoding="utf-8")
        self.write(root, "docs/b.md", "# changed\n")
        _, report = self.relink(root, "--delete", "docs/b.md", "--no-pair")
        plan.write_text(json.dumps(report), encoding="utf-8")
        self.assertIn("uncommitted changes that git rm", run_main(["apply", "--plan", str(plan), "--allow-manual"])[1])

    def test_case_only_rename_and_partial_failure(self) -> None:
        root = self.build()
        _, report = self.relink(root, "--from", "docs/b.md", "--to", "docs/B.md", "--no-pair")
        plan = self.tmp / "c.json"
        plan.write_text(json.dumps(report), encoding="utf-8")
        code, out, _ = run_main(["apply", "--plan", str(plan)])
        self.assertEqual(code, 0, out)
        self.assertIn("docs/B.md", self.git(root, "ls-files"))
        root2 = self.build.__func__(self) if False else self.repo("second", {"a.md": "#", "b.md": "#", "c.md": "[a](a.md)\n"})
        _, report = self.relink(root2, "--from", "a.md", "--to", "x/a.md", "--delete", "b.md")
        plan.write_text(json.dumps(report), encoding="utf-8")
        original = doctidy._git

        def failing(root_: Path, *args: str) -> None:
            if args[0] == "rm":
                raise subprocess.CalledProcessError(1, "git rm")
            original(root_, *args)

        with mock.patch.object(doctidy, "_git", side_effect=failing):
            code, out, _ = run_main(["apply", "--plan", str(plan)])
        self.assertEqual(code, doctidy.EXIT_FINDINGS)
        self.assertIn("STOPPED PART-WAY", out)
        self.assertIn("git mv 'x/a.md' 'a.md'", out)
        self.assertIn("reverse the substitutions", out)

    def test_manual_edits_are_not_reported_again_as_text_references(self) -> None:
        root = self.build(**{"notes.md": "[a](docs/a.md)\n\n```\n[a](docs/a.md)\n```\n"})
        _, report = self.relink(root, "--from", "docs/a.md", "--to", "archive/docs/a.md", "--no-pair")
        self.assertIn(("notes.md", 1), [(m["file"], m["line"]) for m in report["manual"]])
        refs = {(t["file"], t["line"]) for t in report["textRefs"]}
        self.assertNotIn(("notes.md", 1), refs)
        self.assertIn(("notes.md", 4), refs)

    def test_save_writes_json_outside_the_repository(self) -> None:
        root = self.build()
        saved = self.tmp / "run" / "plan-01.json"
        code, out, _ = run_main(["relink", str(root), "--delete", "docs/b.md", "--save", str(saved)])
        self.assertEqual(code, 0)
        self.assertIn("doc-tidy relink", out)
        self.assertEqual(json.loads(saved.read_text(encoding="utf-8"))["command"], "relink")
        code, _, err = run_main(["relink", str(root), "--delete", "docs/b.md", "--save", str(root / "plan.json")])
        self.assertEqual(code, doctidy.EXIT_USAGE)
        self.assertIn("outside the repository", err)
        config = self.tmp / "cfg"
        config.mkdir()
        run_main(["plans", "--config-dir", str(config), "--save", str(self.tmp / "run" / "plans.json")])
        run_main(["sweep", str(root), "--save", str(self.tmp / "run" / "sweep.json")])
        self.assertEqual(sorted(p.name for p in (self.tmp / "run").iterdir()), ["plan-01.json", "plans.json", "sweep.json"])

    def test_relink_usage_errors(self) -> None:
        root = self.build()
        for args, message in (
            (["relink", str(root)], "give at least one"),
            (["relink", str(root), "--from", "docs/a.md"], "same number of times"),
            (["relink", str(root), "--merge", "docs/a.md"], "SRC:DST"),
            (["relink", str(root / "README.md"), "--delete", "x"], "not a directory"),
        ):
            with self.subTest(args=args):
                code, _, err = run_main(args)
                self.assertEqual(code, doctidy.EXIT_USAGE)
                self.assertIn(message, err)

    def test_render_destination_styles(self) -> None:
        render = doctidy.render_destination
        root = Path("/work/repo")
        self.assertEqual(render("./a.md", "inline", "relative", "x.md", "d/a.md", "repo", root), "./d/a.md")
        self.assertEqual(render("a%20b.md", "inline", "relative", "x.md", "d/a b.md", "repo", root), "d/a%20b.md")
        self.assertEqual(render("repo/a.md", "mention", "prefixed", "x.md", "d/a.md", "repo", root), "repo/d/a.md")
        self.assertEqual(render("/work/repo/a.md", "mention", "absolute", "x.md", "d/a.md", "repo", root), "/work/repo/d/a.md")
        self.assertEqual(render("a.md#x", "mention", "basename", "x.md", "d/b.md", "repo", root), "b.md#x")
        with mock.patch.object(doctidy.Path, "home", return_value=Path("/work")):
            self.assertEqual(render("~/repo/a.md", "mention", "home", "x.md", "d/a.md", "repo", root), "~/repo/d/a.md")
            self.assertEqual(render("~/repo/a.md", "mention", "home", "x.md", "d/a.md", "repo", Path("/else")), "/else/d/a.md")
        self.assertEqual(render("../..", "inline", "relative", "a/b/c.md", "", "repo", root), "../..")


if __name__ == "__main__":
    unittest.main()
