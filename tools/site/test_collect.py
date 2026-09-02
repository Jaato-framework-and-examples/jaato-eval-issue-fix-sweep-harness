"""Tests for the site collector.

``unittest`` rather than pytest on purpose: the whole point of collect.py is
that publishing needs no dependencies, and a test suite that needs one would
undo that for CI.

    python -m unittest discover -s tools/site

The cases here are the ones where a plausible implementation is silently
WRONG rather than broken — a range that counts an unreported value as zero, a
pass rate that reads "never exercised" as "always failed", an absent payload
counted as a matching one.  A collector that fails those still produces a
page; it just produces a page that lies.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from collect import (BLOCKED, Cell, CorpusError, FAIL, PASS, build,
                     build_range, unslug_repo, _agreement, _cache_hit_share)


def _row(profile_set="m", state=PASS, arm="a", **kw):
    row = {"arm_id": f"t@{profile_set}#{arm}", "profile_set": profile_set, "state": state}
    row.update(kw)
    return row


def _corpus(tmp, files):
    """files: {(repo_slug, issue, run): [rows]} -> a sweeps dir."""
    root = Path(tmp) / "sweeps"
    for (repo, issue, run), rows in files.items():
        d = root / repo / issue
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{run}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return root


class TestRange(unittest.TestCase):
    def test_endpoints_are_attributed(self):
        r = build_range([(3.0, "c"), (1.0, "a"), (2.0, "b")])
        self.assertEqual((r["min"], r["min_by"], r["max"], r["max_by"]), (1.0, "a", 3.0, "c"))
        self.assertEqual((r["n"], r["of"]), (3, 3))

    def test_unreported_values_are_dropped_not_zeroed(self):
        """A provider that reported no cost is not an arm that cost nothing."""
        r = build_range([(None, "a"), (2.0, "b")])
        self.assertEqual((r["min"], r["max"]), (2.0, 2.0))
        self.assertEqual((r["n"], r["of"]), (1, 2))

    def test_nothing_observed_is_none(self):
        self.assertIsNone(build_range([(None, "a"), (None, "b")]))
        self.assertIsNone(build_range([]))

    def test_booleans_are_not_numbers(self):
        self.assertIsNone(build_range([(True, "a")]))


class TestCell(unittest.TestCase):
    def test_blocked_is_never_in_the_denominator(self):
        cell = Cell()
        for state in (PASS, FAIL, BLOCKED, BLOCKED):
            cell.add(_row(state=state))
        self.assertEqual(cell.exercised, 2)
        self.assertAlmostEqual(cell.pass_rate, 0.5)

    def test_blocked_is_always_counted(self):
        cell = Cell()
        cell.add(_row(state=BLOCKED))
        self.assertEqual(cell.blocked, 1)

    def test_never_exercised_is_none_not_zero(self):
        """Zero says 'it always failed'; the truth is 'we never found out'."""
        cell = Cell()
        cell.add(_row(state=BLOCKED))
        self.assertIsNone(cell.pass_rate)


class TestAgreement(unittest.TestCase):
    """The definition is jaato_eval's (#798). These pin the three rules that
    were each a defect before it, so a refactor cannot quietly restore one."""

    def test_one_answer_is_not_agreement_nor_disagreement(self):
        """Was 100% 'byte-identical across repeats' from a single payload."""
        a = _agreement([_row(payload_hash="x"), _row(payload_hash=None)])
        self.assertIsNone(a["share"])
        self.assertEqual((a["answered"], a["exercised"]), (1, 2))

    def test_nothing_matched_is_zero_not_one_over_n(self):
        """Was 50% across two arms and 25% across four — the same printed
        number meaning 'nothing matched' in one cell and 'half matched' in
        another."""
        two = _agreement([_row(payload_hash="x"), _row(payload_hash="y")])
        four = _agreement([_row(payload_hash=h) for h in "wxyz"])
        self.assertEqual(two["share"], 0.0)
        self.assertEqual(four["share"], 0.0)

    def test_modal_share_counts_arms_not_distinct_hashes(self):
        """Two of three agreeing is 67%, not 1/2 distinct hashes = 50%."""
        a = _agreement([_row(payload_hash="x"), _row(payload_hash="x"),
                        _row(payload_hash="y")])
        self.assertAlmostEqual(a["share"], 2 / 3)

    def test_a_silent_arm_stays_in_the_denominator(self):
        """It did not disagree, but it did not reproduce the answer either."""
        a = _agreement([_row(payload_hash="x"), _row(payload_hash="x"),
                        _row(payload_hash=None)])
        self.assertAlmostEqual(a["share"], 2 / 3)
        self.assertEqual((a["answered"], a["exercised"]), (2, 3))

    def test_blocked_arms_leave_the_denominator(self):
        """`exercised` is PASS + FAIL, as everywhere else in this file."""
        a = _agreement([_row(payload_hash="x"), _row(payload_hash="x"),
                        _row(state=BLOCKED)])
        self.assertAlmostEqual(a["share"], 1.0)


class TestCacheHitShare(unittest.TestCase):
    """Share of BILLED tokens served from cache — both figures the same shape."""

    def test_computed_from_spend_figures(self):
        row = _row(usage={"spend_cache_read_tokens": 250, "spend_total_tokens": 1000})
        self.assertAlmostEqual(_cache_hit_share(row), 0.25)

    def test_absent_spend_field_is_not_observed(self):
        """The archived corpus: no runner recorded it, so there is no answer."""
        self.assertIsNone(_cache_hit_share(_row(usage={"spend_total_tokens": 1000})))

    def test_never_falls_back_to_the_last_response_level(self):
        """`cache_read_tokens` is the LAST RESPONSE's reading, summed across
        turns by jaato-eval — neither a level nor a spend. Reaching for it
        would publish that artifact under a label that means something else."""
        row = _row(usage={"cache_read_tokens": 900, "spend_total_tokens": 1000})
        self.assertIsNone(_cache_hit_share(row))

    def test_zero_denominator_does_not_divide(self):
        row = _row(usage={"spend_cache_read_tokens": 5, "spend_total_tokens": 0})
        self.assertIsNone(_cache_hit_share(row))

    def test_ranges_over_arms_once_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _corpus(tmp, {("Owner__name", "700", "run1"): [
                _row("a", PASS, "0", usage={"spend_cache_read_tokens": 100,
                                            "spend_total_tokens": 1000}),
                _row("a", PASS, "1", usage={"spend_cache_read_tokens": 800,
                                            "spend_total_tokens": 1000}),
                _row("a", PASS, "2", usage={"spend_total_tokens": 1000}),
            ]})
            model = build(root)["issues"][0]["models"][0]
            share = model["cache_hit_share"]
            self.assertAlmostEqual(share["min"], 0.1)
            self.assertAlmostEqual(share["max"], 0.8)
            # The arm that recorded nothing is not a 0% cache hit.
            self.assertEqual((share["n"], share["of"]), (2, 3))

    def test_real_corpus_reports_nothing_rather_than_an_artifact(self):
        root = Path(__file__).resolve().parents[2] / "sweeps"
        if not root.is_dir():
            self.skipTest("no sweeps/ beside this checkout")
        for issue in build(root)["issues"]:
            self.assertIsNone(issue["cache_hit_share"])
            for model in issue["models"]:
                self.assertIsNone(model["cache_hit_share"])


class TestPathIsMetadata(unittest.TestCase):
    def test_unslug(self):
        self.assertEqual(unslug_repo("Owner__name"), "Owner/name")

    def test_ambiguous_slug_is_refused_not_guessed(self):
        with self.assertRaises(CorpusError):
            unslug_repo("Owner__weird__name")
        with self.assertRaises(CorpusError):
            unslug_repo("noseparator")

    def test_issue_url_comes_from_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _corpus(tmp, {("Owner__name", "700", "run1"): [_row()]})
            doc = build(root)
            self.assertEqual(doc["issues"][0]["url"],
                             "https://github.com/Owner/name/issues/700")


class TestRefusals(unittest.TestCase):
    def _refuses(self, files, fragment):
        with tempfile.TemporaryDirectory() as tmp:
            root = _corpus(tmp, files)
            with self.assertRaises(CorpusError) as caught:
                build(root)
            self.assertIn(fragment, str(caught.exception))

    def test_unparseable_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _corpus(tmp, {("Owner__name", "700", "run1"): [_row()]})
            (root / "Owner__name" / "700" / "run1.jsonl").write_text('{"arm_id":\n')
            with self.assertRaises(CorpusError):
                build(root)

    def test_unknown_state(self):
        self._refuses({("Owner__name", "700", "run1"): [_row(state="WOBBLY")]},
                      "unknown state")

    def test_missing_key(self):
        self._refuses({("Owner__name", "700", "run1"): [{"arm_id": "x", "state": PASS}]},
                      "profile_set")

    def test_empty_run_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _corpus(tmp, {("Owner__name", "700", "run1"): [_row()]})
            (root / "Owner__name" / "700" / "run1.jsonl").write_text("")
            with self.assertRaises(CorpusError):
                build(root)

    def test_file_at_the_wrong_depth_is_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _corpus(tmp, {("Owner__name", "700", "run1"): [_row()]})
            (root / "Owner__name" / "stray.jsonl").write_text(json.dumps(_row()) + "\n")
            with self.assertRaises(CorpusError):
                build(root)


class TestLevels(unittest.TestCase):
    def setUp(self):
        # Two runs of one issue: model 'a' passes then fails, model 'b' runs
        # once.  Enough to separate per-arm from per-run populations.
        self.files = {
            ("Owner__name", "700", "run1"): [
                _row("a", PASS, "0", usage={"cost_usd": 1.0}, payload_hash="h1"),
                _row("a", PASS, "1", usage={"cost_usd": 3.0}, payload_hash="h1"),
                _row("b", PASS, "0", usage={"cost_usd": 9.0}, payload_hash="h2"),
            ],
            ("Owner__name", "700", "run2"): [
                _row("a", FAIL, "0", usage={"cost_usd": 2.0}, payload_hash="h3"),
                _row("a", PASS, "1", usage={"cost_usd": 2.5}),
            ],
        }

    def _issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            return build(_corpus(tmp, self.files))["issues"][0]

    def test_pass_rate_ranges_over_runs_not_arms(self):
        model_a = [m for m in self._issue()["models"] if m["profile_set"] == "a"][0]
        # run1: 2/2 = 100%.  run2: 1/2 = 50%.  Two runs, so n=2 — not n=4.
        self.assertEqual((model_a["pass_rate"]["min"], model_a["pass_rate"]["max"]), (0.5, 1.0))
        self.assertEqual(model_a["pass_rate"]["n"], 2)
        self.assertEqual(model_a["pass_rate"]["min_by"], "run2")

    def test_cost_ranges_over_arms_and_names_the_arm(self):
        model_a = [m for m in self._issue()["models"] if m["profile_set"] == "a"][0]
        self.assertEqual(model_a["cost_usd"]["n"], 4)
        self.assertEqual(model_a["cost_usd"]["min"], 1.0)
        self.assertEqual(model_a["cost_usd"]["max"], 3.0)
        self.assertIn("#0", model_a["cost_usd"]["min_by"])

    def test_issue_level_attributes_endpoints_to_models(self):
        issue = self._issue()
        self.assertEqual(issue["cost_usd"]["max_by"], "b")
        self.assertEqual(issue["cost_usd"]["min_by"], "a")

    def test_totals_cover_only_what_was_reported(self):
        model_a = [m for m in self._issue()["models"] if m["profile_set"] == "a"][0]
        self.assertAlmostEqual(model_a["cost_usd_total"], 8.5)

    def test_runs_are_listed_per_model(self):
        models = {m["profile_set"]: m for m in self._issue()["models"]}
        self.assertEqual(models["a"]["runs"], ["run1", "run2"])
        self.assertEqual(models["b"]["runs"], ["run1"])

    def test_arm_detail_carries_its_run(self):
        model_a = [m for m in self._issue()["models"] if m["profile_set"] == "a"][0]
        self.assertEqual([a["run"] for a in model_a["arm_detail"]],
                         ["run1", "run1", "run2", "run2"])

    def test_repointed_profile_set_keeps_both_models(self):
        """A profile set repointed between runs is a confound worth seeing,
        so the value is a list rather than whichever row happened to be last."""
        self.files[("Owner__name", "700", "run2")][0]["model"] = "new/model"
        self.files[("Owner__name", "700", "run1")][0]["model"] = "old/model"
        model_a = [m for m in self._issue()["models"] if m["profile_set"] == "a"][0]
        self.assertEqual(model_a["models"], ["new/model", "old/model"])


class TestRealCorpus(unittest.TestCase):
    """The shipped corpus must build, and its known facts must survive."""

    def setUp(self):
        root = Path(__file__).resolve().parents[2] / "sweeps"
        if not root.is_dir():
            self.skipTest("no sweeps/ beside this checkout")
        self.doc = build(root)

    def test_every_issue_builds(self):
        self.assertEqual([i["issue"] for i in self.doc["issues"]], ["715", "782"])

    def test_the_max_tokens_arm_is_not_counted_as_agreeing(self):
        """run22's gemini arms: one payload observed, two arms. That cell
        printed 100% 'byte-identical across repeats' before jaato #798."""
        issue = [i for i in self.doc["issues"] if i["issue"] == "715"][0]
        gemini = [m for m in issue["models"] if m["profile_set"] == "openrouter_gemini25flash"][0]
        self.assertIsNone(gemini["agreement"]["share"])
        self.assertEqual(gemini["agreement"]["answered"], 1)

    def test_no_two_arms_have_ever_agreed(self):
        """Every cell where two arms answered is 0%: at temperature 0.0, no
        two arms of this corpus produced the same payload."""
        for issue in self.doc["issues"]:
            for model in issue["models"]:
                share = model["agreement"]["share"]
                self.assertIn(share, (None, 0.0), f"{issue['issue']}/{model['profile_set']}")

    def test_nudge_ceiling_arm_survives_as_a_figure(self):
        issue = [i for i in self.doc["issues"] if i["issue"] == "715"][0]
        gemini = [m for m in issue["models"] if m["profile_set"] == "openrouter_gemini25flash"][0]
        self.assertEqual(gemini["completion_nudges"]["max"], 2)


if __name__ == "__main__":
    unittest.main()
