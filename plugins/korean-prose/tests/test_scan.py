"""Tests for scripts/scan.py. Standard library only.

Run from anywhere:
    python3 -B -m unittest discover -s <skill-or-plugin-root>/tests -v

Markdown fixtures are written to a temporary directory by the tests
themselves rather than committed, so no fixture file carries front matter
that a Markdown frontmatter linter would pick up.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scan  # noqa: E402

P_HEADER = "\t".join(scan.PATTERN_COLUMNS)
T_HEADER = "\t".join(scan.TERM_COLUMNS)


def pattern_row(pid="p1", severity="red", category="translationese", regex="에 있어서",
                min_count="1", example="운영에 있어서", why="why", suggestion="try") -> str:
    return "\t".join((pid, severity, category, regex, min_count, example, why, suggestion))


def term_row(canonical="Canary", variants="카나리", kind="concept", note="") -> str:
    return "\t".join((canonical, variants, kind, note))


class LexiconDir:
    """A throwaway lexicon directory."""

    def __init__(self, patterns: list[str] | None = None, terms: list[str] | None = None,
                 local_patterns: list[str] | None = None, local_terms: list[str] | None = None,
                 raw_patterns: str | None = None):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        body = raw_patterns if raw_patterns is not None else "\n".join([P_HEADER, *(patterns or [])]) + "\n"
        (self.path / "patterns.tsv").write_text(body, encoding="utf-8")
        (self.path / "terms.tsv").write_text("\n".join([T_HEADER, *(terms or [])]) + "\n", encoding="utf-8")
        if local_patterns is not None:
            (self.path / "patterns.local.tsv").write_text("\n".join([P_HEADER, *local_patterns]) + "\n", encoding="utf-8")
        if local_terms is not None:
            (self.path / "terms.local.tsv").write_text("\n".join([T_HEADER, *local_terms]) + "\n", encoding="utf-8")

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


def run_main(argv: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    patch = mock.patch.object(sys, "stdin", io.StringIO(stdin)) if stdin is not None else contextlib.nullcontext()
    with patch, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------


class ShippedLexiconTest(unittest.TestCase):
    def test_shipped_lexicon_has_no_problems(self):
        patterns, terms, problems = scan.load_lexicon(scan.LEXICON_DIR)
        self.assertEqual(problems, [])
        self.assertTrue(patterns)
        self.assertTrue(terms)

    def test_every_shipped_example_is_reported_by_a_scan(self):
        patterns, terms, _ = scan.load_lexicon(scan.LEXICON_DIR)
        for pattern in patterns:
            text = "\n".join([pattern.example] * pattern.min_count)
            report = scan.scan_text(text, "text", [pattern], [])
            with self.subTest(pattern=pattern.id):
                self.assertEqual([hit["id"] for hit in report["patterns"]], [pattern.id])

    def test_canary_spelled_three_ways_is_reported(self):
        # Regression: the reviewer missed `카나리` next to `Canary` until the user pointed at it.
        patterns, terms, _ = scan.load_lexicon(scan.LEXICON_DIR)
        text = "카나리 배포로 전환\nArgo Rollouts Canary 전략\ncanary 단계에서 검증\n"
        report = scan.scan_text(text, "text", patterns, terms)
        canary = next(t for t in report["terms"] if t["canonical"] == "Canary")
        self.assertEqual({f["form"]: f["lines"] for f in canary["forms"]},
                         {"카나리": [1], "Canary": [2], "canary": [3]})


    def test_english_only_lines_are_out_of_scope(self):
        patterns, terms, _ = scan.load_lexicon(scan.LEXICON_DIR)
        text = "The incident was a Canary rollout.\nWebhook and webhook\n카나리 배포\n"
        report = scan.scan_text(text, "text", patterns, terms)
        self.assertEqual((report["patterns"], report["terms"], report["case_variants"]), ([], [], []))

    def test_glossed_english_in_parentheses_is_not_untranslated(self):
        patterns, _, _ = scan.load_lexicon(scan.LEXICON_DIR)
        (row,) = [p for p in patterns if p.id == "untranslated-noun"]
        self.assertEqual(scan.scan_text("로드밸런서 미생성(silent failure) 해결", "text", [row], [])["patterns"], [])
        self.assertTrue(scan.scan_text("silent failure 가 발생", "text", [row], [])["patterns"])


    def test_corpus_false_positives_stay_fixed(self):
        # Each line was a false positive measured on a real Korean corpus (resume and internal docs).
        patterns, _, _ = scan.load_lexicon(scan.LEXICON_DIR)
        quiet = [
            "감시 대상과 같은 클러스터 안에 있어 함께 멈췄습니다",
            "세션은 Redis에 있어 Pod가 옮겨 가도 끊기지 않음",
            "프로비저닝 5분 → 약 10초(약 97% 단축)",
            "소스 페치 시간 약 78% 단축",
            "| 공유 경로 | /volume1/nfs |",
            "그룹이 비면 그 역할을 가진 사람이 없습니다",
            "\"무손실\"이 아니라 유계 손실",
            "CNI 버전 통제 불가",
        ]
        for line in quiet:
            with self.subTest(line=line):
                self.assertEqual(scan.scan_text(line, "text", patterns, [])["patterns"], [])
        hit = scan.scan_text("CI 프로비저닝 약 97% 단축", "text", patterns, [])["patterns"]
        self.assertEqual([h["id"] for h in hit], ["unit-drop"])


class LexiconValidationTest(unittest.TestCase):
    def problems(self, **kwargs) -> list[str]:
        with LexiconDir(**kwargs) as path:
            return scan.load_lexicon(path)[2]

    def test_valid_lexicon(self):
        self.assertEqual(self.problems(patterns=[pattern_row()], terms=[term_row()]), [])

    def test_comments_and_blank_lines_are_skipped(self):
        raw = "# comment\n\n" + P_HEADER + "\n# another\n" + pattern_row() + "\n"
        self.assertEqual(self.problems(raw_patterns=raw), [])

    def test_wrong_header(self):
        self.assertIn("header must be", self.problems(raw_patterns="id\tregex\n")[0])

    def test_missing_header(self):
        self.assertIn("missing header", self.problems(raw_patterns="# only a comment\n")[0])

    def test_wrong_column_count(self):
        self.assertIn("expected 8 columns", self.problems(patterns=["a\tb"])[0])

    def test_empty_id(self):
        self.assertIn("empty id", self.problems(patterns=[pattern_row(pid="")])[0])

    def test_duplicate_id(self):
        self.assertIn("duplicate id", self.problems(patterns=[pattern_row(), pattern_row()])[0])

    def test_bad_severity(self):
        self.assertIn("severity", self.problems(patterns=[pattern_row(severity="orange")])[0])

    def test_min_count_not_integer(self):
        self.assertIn("not N or file:N", self.problems(patterns=[pattern_row(min_count="many")])[0])
        self.assertIn("not N or file:N", self.problems(patterns=[pattern_row(min_count="file:x")])[0])

    def test_min_count_below_one(self):
        self.assertIn(">= 1", self.problems(patterns=[pattern_row(min_count="0")])[0])

    def test_regex_does_not_compile(self):
        self.assertIn("does not compile", self.problems(patterns=[pattern_row(regex="(")])[0])

    def test_empty_example(self):
        self.assertIn("no example", self.problems(patterns=[pattern_row(example="")])[0])

    def test_example_must_match_regex(self):
        self.assertIn("does not match", self.problems(patterns=[pattern_row(example="자연스러운 문장")])[0])

    def test_empty_canonical(self):
        self.assertIn("empty canonical", self.problems(terms=[term_row(canonical="")])[0])

    def test_duplicate_canonical(self):
        self.assertIn("duplicate canonical", self.problems(terms=[term_row(), term_row()])[0])

    def test_bad_kind(self):
        self.assertIn("kind", self.problems(terms=[term_row(kind="thing")])[0])

    def test_surface_claimed_by_two_terms(self):
        found = self.problems(terms=[term_row("Canary", "카나리"), term_row("Canary release", "canary")])
        self.assertIn("claimed by both", found[0])

    def test_keep_term_flagged_by_a_pattern(self):
        found = self.problems(patterns=[pattern_row(regex="cutover", example="cutover 일정")],
                              terms=[term_row("cutover", "컷오버", "keep")])
        self.assertIn("keep term 'cutover' is flagged", found[0])

    def test_missing_lexicon_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(scan.LexiconError):
            scan.load_lexicon(Path(tmp))

    def test_local_overlay_overrides_by_id_and_appends(self):
        with LexiconDir(patterns=[pattern_row(why="base")],
                        local_patterns=[pattern_row(why="local"), pattern_row(pid="p2", regex="함정", example="함정")],
                        local_terms=[term_row("Canary", "카나리|캐너리"), term_row("Pod", "파드", "k8s-kind")],
                        terms=[term_row()]) as path:
            patterns, terms, problems = scan.load_lexicon(path)
        self.assertEqual(problems, [])
        self.assertEqual({p.id: p.why for p in patterns}, {"p1": "local", "p2": "why"})
        self.assertEqual({t.canonical: t.surfaces for t in terms},
                         {"Canary": ("Canary", "카나리", "캐너리"), "Pod": ("Pod", "파드")})


class MarkdownExtractionTest(unittest.TestCase):
    def clean(self, text: str) -> dict[int, str]:
        return {line.lineno: line.clean for line in scan.extract_markdown(text)}

    def test_front_matter_is_skipped(self):
        lines = self.clean("---\ntitle: 에 있어서\n---\n본문\n")
        self.assertEqual(list(lines), [4])

    def test_unterminated_front_matter_is_prose(self):
        self.assertEqual(list(self.clean("---\n본문\n")), [1, 2])

    def test_fenced_blocks_are_skipped(self):
        text = "앞\n```bash\n에 있어서\n```\n가운데\n~~~~\n~~~\n에 있어서\n~~~~\n뒤\n"
        self.assertEqual(self.clean(text), {1: "앞", 5: "가운데", 10: "뒤"})

    def test_inline_markup_is_blanked_but_length_is_kept(self):
        raw = "`에 있어서` 와 <code>함정</code> 그리고 [링크](https://example.com/함정) <b>굵게</b> https://x.io/압력"
        (line,) = scan.extract_markdown(raw)
        self.assertEqual(len(line.clean), len(raw))
        for hidden in ("에 있어서", "함정", "압력", "https", "<b>"):
            self.assertNotIn(hidden, line.clean)
        for kept in ("링크", "굵게", "그리고"):
            self.assertIn(kept, line.clean)


class YamlExtractionTest(unittest.TestCase):
    def values(self, text: str) -> dict[int, str]:
        return {line.lineno: line.clean.strip() for line in scan.extract_yaml(text)}

    def test_scalar_forms(self):
        text = '\n'.join([
            'title:',
            '  ko: "큰따옴표 \\"값\\""',
            "  en: \"English\"",
            "  - ko: '작은따옴표 ''값'''",
            "  - ko: 평문 값 # comment",
            '  label: { ko: "플로우", en: "Flow" }',
        ])
        self.assertEqual(self.values(text), {
            2: '큰따옴표 \\"값\\"',
            4: "작은따옴표 ''값''",
            5: "평문 값",
            6: "플로우",
        })

    def test_value_keeps_its_column(self):
        (line,) = scan.extract_yaml('    - ko: "값"')
        self.assertEqual(line.raw.index("값"), 11)
        self.assertEqual(line.raw[:11].strip(), "")

    def test_block_literal_ko_is_emitted_and_en_is_skipped(self):
        text = "\n".join([
            "intro:",
            "  ko: |-",
            "    첫 문단",
            "",
            "    둘째 문단",
            "  en: |-",
            "    ko: \"not prose\"",
            "    English",
            "next: 1",
        ])
        self.assertEqual(self.values(text), {3: "첫 문단", 5: "둘째 문단"})

    def test_markup_inside_a_value_is_blanked(self):
        (line,) = scan.extract_yaml('ko: "<strong>라벨</strong>: <code>에 있어서</code> 본문"')
        self.assertNotIn("에 있어서", line.clean)
        self.assertIn("<strong>", line.raw)
        self.assertIn("라벨", line.clean)


class PatternPassTest(unittest.TestCase):
    def lexicon(self, *rows: str):
        with LexiconDir(patterns=list(rows)) as path:
            patterns, terms, problems = scan.load_lexicon(path)
        self.assertEqual(problems, [])
        return patterns

    def test_min_count_threshold(self):
        patterns = self.lexicon(pattern_row(regex="통해", example="통해", min_count="3"))
        lines = scan.extract_text("통해\n통해\n")
        self.assertEqual(scan.scan_patterns(lines, patterns), [])
        lines = scan.extract_text("통해\n통해 통해\n")
        (hit,) = scan.scan_patterns(lines, patterns)
        self.assertEqual((hit["count"], [o["line"] for o in hit["occurrences"]]), (3, [1, 2, 2]))

    def test_min_count_is_counted_per_paragraph(self):
        patterns = self.lexicon(pattern_row(regex="통해", example="통해", min_count="2"))
        self.assertEqual(scan.scan_patterns(scan.extract_text("통해\n\n통해\n"), patterns), [])
        (hit,) = scan.scan_patterns(scan.extract_text("통해\n\n통해 통해\n"), patterns)
        self.assertEqual([o["line"] for o in hit["occurrences"]], [3, 3])

    def test_file_scoped_min_count_spans_paragraphs(self):
        patterns = self.lexicon(pattern_row(regex="통해", example="통해", min_count="file:2"))
        (hit,) = scan.scan_patterns(scan.extract_text("통해\n\n통해\n"), patterns)
        self.assertEqual((hit["count"], hit["count_scope"]), (2, "file"))
        self.assertEqual(scan.scan_patterns(scan.extract_text("통해\n"), patterns), [])

    def test_yaml_paragraphs_are_values_and_block_literal_paragraphs(self):
        lines = scan.extract_yaml("a:\n  ko: \"통해\"\nb:\n  ko: \"통해\"\nc:\n  ko: |-\n    통해\n\n    통해\n")
        self.assertEqual(len({line.block for line in lines}), 4)

    def test_markdown_fence_ends_a_paragraph(self):
        lines = scan.extract_markdown("통해\n```\ncode\n```\n통해\n")
        self.assertNotEqual(lines[0].block, lines[1].block)

    def test_raw_target_sees_markup(self):
        patterns = self.lexicon(pattern_row(regex="(?#raw)</code> 를", example="</code> 를"))
        lines = scan.extract_markdown("<code>x</code> 를 실행")
        self.assertEqual(scan.scan_patterns(lines, patterns)[0]["occurrences"][0]["col"], 8)

    def test_clean_target_does_not_see_markup(self):
        patterns = self.lexicon(pattern_row(regex="</code>", example="</code>"))
        self.assertEqual(scan.scan_patterns(scan.extract_markdown("<code>x</code>"), patterns), [])

    def test_empty_matches_are_ignored(self):
        patterns = self.lexicon(pattern_row(regex="z*", example="z"))
        self.assertEqual(scan.scan_patterns(scan.extract_text("가나다"), patterns), [])

    def test_results_sorted_by_severity_then_id(self):
        patterns = self.lexicon(
            pattern_row("b", "green", regex="가", example="가"),
            pattern_row("a", "yellow", regex="가", example="가"),
            pattern_row("c", "red", regex="가", example="가"),
        )
        hits = scan.scan_patterns(scan.extract_text("가"), patterns)
        self.assertEqual([h["id"] for h in hits], ["c", "a", "b"])


class TermPassTest(unittest.TestCase):
    def terms(self, *rows: str):
        with LexiconDir(terms=list(rows)) as path:
            _, terms, problems = scan.load_lexicon(path)
        self.assertEqual(problems, [])
        return terms

    def test_single_spelling_is_not_reported(self):
        terms = self.terms(term_row())
        self.assertEqual(scan.scan_terms(scan.extract_text("Canary\nCanary"), terms), [])

    def test_plural_is_folded_into_the_singular(self):
        terms = self.terms(term_row("Pod", "파드", "k8s-kind"))
        (result,) = scan.scan_terms(scan.extract_text("Pods 재시작\n파드 목록"), terms)
        self.assertEqual({f["form"] for f in result["forms"]}, {"Pod", "파드"})

    def test_longer_surface_wins_the_overlap(self):
        terms = self.terms(term_row("Blue-Green", "Blue|블루그린"))
        (result,) = scan.scan_terms(scan.extract_text("Blue-Green 배포\n블루그린 배포"), terms)
        self.assertEqual({f["form"]: f["count"] for f in result["forms"]}, {"Blue-Green": 1, "블루그린": 1})

    def test_latin_word_boundaries(self):
        terms = self.terms(term_row("Pod", "파드", "k8s-kind"))
        self.assertEqual(scan.scan_terms(scan.extract_text("podman 과 PodDisruptionBudget\n파드"), terms), [])

    def test_hyphenated_compound_is_not_the_bare_term(self):
        terms = self.terms(term_row("Docker", "도커", "name"))
        self.assertEqual(scan.scan_terms(scan.extract_text("docker-compose 와 Docker"), terms), [])
        self.assertEqual(scan.scan_terms(scan.extract_text("kube-docker 와 Docker"), terms), [])

    def test_hangul_surface_inside_a_longer_word_is_ignored(self):
        terms = self.terms(term_row("Canary", "카나리"))
        self.assertEqual(scan.scan_terms(scan.extract_text("Canary\n노란 스카나리"), terms), [])

    def test_forms_ordered_by_count(self):
        terms = self.terms(term_row())
        (result,) = scan.scan_terms(scan.extract_text("카나리\nCanary Canary"), terms)
        self.assertEqual([f["form"] for f in result["forms"]], ["Canary", "카나리"])


class CaseVariantPassTest(unittest.TestCase):
    def groups(self, text: str, lane: str = "text", terms=()) -> dict[str, dict]:
        lines = scan.EXTRACTORS[lane](text)
        return {g["key"]: g for g in scan.scan_case_variants(lines, list(terms))}

    def test_label_capitalisation_hint(self):
        text = '- ko: "<strong>Stateless 구조</strong>: stateless HTTP"'
        group = self.groups(text, "yaml")["stateless"]
        self.assertEqual(group["hint"], "label-capitalization")

    def test_title_case_phrase_hint(self):
        group = self.groups("Deregistration Delay(30초)\n등록 해제 delay 조정")["delay"]
        self.assertEqual(group["hint"], "title-case-phrase")

    def test_capital_outside_a_phrase_has_no_hint(self):
        group = self.groups("Deregistration Delay 와 Delay 단독\n등록 해제 delay 조정")["delay"]
        self.assertIsNone(group["hint"])

    def test_mid_sentence_capital_has_no_hint(self):
        group = self.groups("구조는 Stateless 이고\nstateless HTTP")["stateless"]
        self.assertIsNone(group["hint"])

    def test_three_spellings_have_no_hint(self):
        self.assertIsNone(self.groups("GitOps\nGitops\ngitops")["gitops"]["hint"])

    def test_two_capitalised_spellings_have_no_hint(self):
        self.assertIsNone(self.groups("GitOps\nGitops")["gitops"]["hint"])

    def test_short_seeded_and_single_spelling_tokens_are_skipped(self):
        terms = [scan.Term("Canary", ("Canary", "카나리"), "concept", "", "terms.tsv")]
        found = self.groups("CI ci\nCanary canary\nHelm", terms=terms)
        self.assertEqual(found, {})

    def test_label_prefix_markers(self):
        for raw, column in (("## Heading", 3), ("> **Bold", 4), ("1. Item", 3), ("| Cell", 2)):
            with self.subTest(raw=raw):
                self.assertTrue(scan.is_label_start(raw, column))
        self.assertFalse(scan.is_label_start("본문 중간 Word", 6))


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_check_lexicon_ok(self):
        code, out, _ = run_main(["--check-lexicon"])
        self.assertEqual(code, scan.EXIT_OK)
        self.assertTrue(out.startswith("lexicon OK"))

    def test_check_lexicon_reports_problems(self):
        with LexiconDir(patterns=[pattern_row(example="nope")]) as path:
            code, out, _ = run_main(["--check-lexicon", "--lexicon", str(path)])
        self.assertEqual(code, scan.EXIT_LEXICON_PROBLEMS)
        self.assertIn("does not match", out)

    def test_scan_refuses_a_broken_lexicon(self):
        target = self.write("a.md", "본문")
        with LexiconDir(patterns=[pattern_row(example="nope")]) as path:
            code, _, err = run_main(["--lexicon", str(path), str(target)])
        self.assertEqual(code, scan.EXIT_USAGE)
        self.assertIn("--check-lexicon", err)

    def test_missing_lexicon_directory(self):
        code, _, err = run_main(["--lexicon", str(self.dir / "absent"), "--check-lexicon"])
        self.assertEqual(code, scan.EXIT_USAGE)
        self.assertIn("not found", err)

    def test_no_input(self):
        code, _, err = run_main([])
        self.assertEqual(code, scan.EXIT_USAGE)
        self.assertIn("--stdin", err)

    def test_not_a_file(self):
        code, _, err = run_main([str(self.dir)])
        self.assertEqual(code, scan.EXIT_USAGE)
        self.assertIn("not a file", err)

    def test_json_report_for_markdown_and_yaml(self):
        md = self.write("doc.md", "운영에 있어서 카나리와 Canary\n")
        yml = self.write("career.yml", 'x:\n  ko: "이것은 테스트"\n  en: "에 있어서"\n')
        code, out, _ = run_main(["--json", str(md), str(yml)])
        self.assertEqual(code, scan.EXIT_OK)
        files = {Path(f["path"]).name: f for f in json.loads(out)["files"]}
        self.assertEqual(files["doc.md"]["lane"], "md")
        self.assertIn("e-isseoseo", [p["id"] for p in files["doc.md"]["patterns"]])
        self.assertEqual(files["doc.md"]["terms"][0]["canonical"], "Canary")
        self.assertEqual(files["career.yml"]["lane"], "yaml")
        self.assertNotIn("e-isseoseo", [p["id"] for p in files["career.yml"]["patterns"]])

    def test_text_report(self):
        many = "\n".join(["운영에 있어서"] * 13)
        target = self.write(
            "notes.txt",
            many + "\n카나리 Canary\n"
            "훅은 WebHook 이고 Webhook 이며\n"
            "- <strong>Sidecar</strong> 와 sidecar\n",
        )
        empty = self.write("clean.txt", "자연스러운 문장입니다.\n")
        code, out, _ = run_main(["--lane", "md", str(target), str(empty)])
        self.assertEqual(code, scan.EXIT_OK)
        self.assertIn("[red] e-isseoseo ×13", out)
        self.assertIn(" …", out)
        self.assertIn("[spelling] Canary (concept)", out)
        self.assertIn("[case] WebHook×1, Webhook×1", out)
        self.assertIn("[spelling] Canary (concept): Canary×1, 카나리×1", out)
        self.assertIn("[case, likely label-capitalization] Sidecar/sidecar", out)
        self.assertIn("(no candidates)", out)

    def test_stdin_defaults_to_text_lane(self):
        code, out, _ = run_main(["--stdin", "--json"], stdin="이것은 테스트이다\n")
        report = json.loads(out)["files"][0]
        self.assertEqual((code, report["path"], report["lane"]), (scan.EXIT_OK, "<stdin>", "text"))
        self.assertEqual(report["patterns"][0]["id"], "it-is-subject")

    def test_stdin_lane_override(self):
        code, out, _ = run_main(["--stdin", "--json", "--lane", "yaml"], stdin='ko: "이것은 값"\n')
        self.assertEqual(json.loads(out)["files"][0]["lane"], "yaml")

    def test_detect_lane(self):
        self.assertEqual(
            [scan.detect_lane(Path(n)) for n in ("a.yaml", "b.YML", "c.markdown", "d.md", "e.txt")],
            ["yaml", "yaml", "md", "md", "text"],
        )


if __name__ == "__main__":
    unittest.main()
