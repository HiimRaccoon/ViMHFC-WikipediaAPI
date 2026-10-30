"""Offline evaluation never promotes suggestions to human truth."""
import csv
import json
from copy import deepcopy

import pytest

from vimfc.experiments.groq_classification import evaluation


def row(page_id, group=None, *, outcome="completed", title="Bài viết", stratum="focus_event"):
    return {
        "input": {"title": title, "lead_text": "Một sự kiện lịch sử.",
                  "sentences": [{"id": "s1", "text": "Một sự kiện lịch sử."}]},
        "evaluation": {"page_id": page_id, "revision_id": 100 + page_id,
                       "snapshot": "2026-01-01", "baseline_outcome": outcome,
                       "baseline_primary": group, "classification_reason": "fixture",
                       "sampling_stratum": stratum, "eligible": group is not None},
        "provenance": {"source": "archive", "reference": "fixture-source"},
        "input_fingerprint": f"fingerprint-{page_id}",
    }


def result(page_id, group="Event", *, status="valid", mode="blind", abstain=False):
    return {"page_id": page_id, "input_fingerprint": f"fingerprint-{page_id}",
            "model_id": "fixture-model", "mode": mode, "status": status,
            "prediction": {"predicted_group": None if abstain else group,
                           "abstain": abstain, "evidence_sentence_ids": ["s1"],
                           "reason": "Nội dung mô tả một sự kiện.", "ambiguity": []}
            if status == "valid" else None,
            "latency_seconds": 2.0, "usage": {"prompt_tokens": 10,
            "completion_tokens": 5, "total_tokens": 15}, "request_key": f"key-{page_id}"}


def reviewed(page_id, group):
    return {"page_id": page_id, "input_fingerprint": f"fingerprint-{page_id}",
            "human_primary": group, "human_abstain": False,
            "review_status": "reviewed", "reviewer": "reviewer-one",
            "review_date": "2026-10-29", "notes": "Checked source."}


def test_no_gold_separates_baseline_agreement_and_three_views():
    rows = [row(1, "Location"), row(2, outcome="unmapped"), row(3, outcome="source_unavailable")]
    metrics = evaluation.evaluate_records(rows, [result(1), result(2), result(3, abstain=True)])
    assert metrics["human_review"] == {"status": "unassessed", "reviewed_count": 0, "metrics": None}
    assert metrics["baseline_agreement"] == {"count": 0, "denominator": 1, "rate": 0.0}
    assert metrics["views"]["baseline_verified_only"]["assigned_count"] == 1
    assert metrics["views"]["blind_groq"]["assigned_count"] == 2
    assert metrics["views"]["wikidata_first"]["assigned_count"] == 2
    assert metrics["views"]["wikidata_first"]["group_counts"] == {"Event": 1, "Location": 1}
    assert metrics["views"]["wikidata_first"]["coverage"] == pytest.approx(2 / 3)
    assert metrics["by_baseline_outcome"]["unmapped"]["count"] == 1
    assert metrics["by_baseline_group"]["Location"]["count"] == 1
    assert metrics["by_focus_stratum"]["focus_event"]["count"] == 3


def test_assisted_bypass_does_not_count_as_blind_prediction_or_agreement():
    metrics = evaluation.evaluate_records([row(1, "Location"), row(2, outcome="unmapped")],
        [result(1, status="skipped_verified", mode="assisted"), result(2, mode="assisted")])
    assert metrics["views"]["blind_groq"]["assigned_count"] == 0
    assert metrics["views"]["wikidata_first"]["assigned_count"] == 2
    assert metrics["baseline_agreement"]["denominator"] == 0
    assert metrics["operations"]["requested_records"] == 1


def test_reviewed_subset_has_real_confusion_and_abstention_as_error():
    rows = [row(1, "Location"), row(2, "Event"), row(3, "Event"), row(4, "Person")]
    results = [result(1), result(2, "Location"), result(3, abstain=True), result(4, "Person")]
    labels = [reviewed(1, "Event"), reviewed(2, "Event"), reviewed(3, "Event")]
    metrics = evaluation.evaluate_records(rows, results, labels)
    human = metrics["human_review"]
    assert human["reviewed_count"] == 3
    blind = human["metrics"]["blind_groq"]
    assert blind["accuracy"] == pytest.approx(1 / 3)
    assert blind["coverage"] == pytest.approx(2 / 3)
    assert blind["per_group"]["Event"]["precision"] == 1.0
    assert blind["per_group"]["Event"]["recall"] == pytest.approx(1 / 3)
    assert blind["per_group"]["Event"]["f1"] == .5
    assert blind["confusion_matrix"]["Event"] == {"Event": 1, "Location": 1, "__abstain__": 1}
    assert blind["macro_f1"] == pytest.approx(1 / 6)
    assert human["metrics"]["wikidata_first"]["accuracy"] == pytest.approx(2 / 3)


@pytest.mark.parametrize("change", [
    {"input_fingerprint": "stale"}, {"page_id": 999}, {"reviewer": "  "},
    {"review_date": "not-a-date"}, {"human_primary": "llm_suggested_primary"},
    {"human_primary": "Event", "human_abstain": True},
])
def test_rejects_invalid_review_bindings_and_provenance(change):
    label = reviewed(1, "Event")
    label.update(change)
    with pytest.raises(ValueError):
        evaluation.evaluate_records([row(1, "Event")], [result(1)], [label])


def test_duplicate_review_rejected_even_when_same_label():
    label = reviewed(1, "Event")
    with pytest.raises(ValueError, match="duplicate"):
        evaluation.evaluate_records([row(1, "Event")], [result(1)], [label, deepcopy(label)])


def test_unreviewed_suggestion_never_becomes_gold_and_human_abstain_is_explicit():
    pending = reviewed(1, "Event")
    pending["review_status"] = "pending"
    pending["reviewer"] = ""
    metrics = evaluation.evaluate_records([row(1, "Event")], [result(1)], [pending])
    assert metrics["human_review"]["metrics"] is None
    pending.update(review_status="reviewed", reviewer="reader", human_primary=None, human_abstain=True)
    metrics = evaluation.evaluate_records([row(1, "Event")], [result(1, abstain=True)], [pending])
    assert metrics["human_review"]["metrics"]["blind_groq"]["accuracy"] == 1.0


def test_invalid_and_missing_results_remain_unresolved_with_operational_denominators():
    rows = [row(i, outcome="unmapped") for i in range(1, 5)]
    results = [result(1), result(2, status="invalid_evidence"), result(3, status="provider_error")]
    metrics = evaluation.evaluate_records(rows, results)
    ops = metrics["operations"]
    assert ops["requested_records"] == 3
    assert ops["status_counts"] == {"valid": 1, "invalid_evidence": 1, "provider_error": 1, "unattempted": 1}
    assert ops["invalid_rate"] == pytest.approx(1 / 3)
    assert ops["invalid_evidence_rate"] == pytest.approx(1 / 3)
    assert ops["latency_seconds"]["mean"] == 2.0
    assert ops["usage"] == {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45}
    assert metrics["views"]["blind_groq"]["unresolved_count"] == 3


@pytest.mark.parametrize("bad", ["fingerprint", "evidence", "duplicate", "unknown_page"])
def test_rejects_stale_or_ambiguous_results_and_unbound_evidence(bad):
    prediction = result(1)
    if bad == "fingerprint":
        prediction["input_fingerprint"] = "wrong"
    elif bad == "evidence":
        prediction["prediction"]["evidence_sentence_ids"] = ["missing"]
    elif bad == "unknown_page":
        prediction["page_id"] = 99
    results = [prediction, deepcopy(prediction)] if bad == "duplicate" else [prediction]
    with pytest.raises(ValueError):
        evaluation.evaluate_records([row(1, "Event")], results)


def test_review_export_has_empty_human_fields_bound_evidence_formula_guard_and_immutable_files(tmp_path):
    rows = [row(1, "Location", title="=HYPERLINK(\"bad\")")]
    results = [result(1)]
    original = deepcopy((rows, results))
    paths = evaluation.export_review(rows, results, tmp_path / "review")
    with paths["review_csv"].open(encoding="utf-8", newline="") as handle:
        record = next(csv.DictReader(handle))
    assert record["title"].startswith("'=HYPERLINK")
    assert record["input_fingerprint"] == "fingerprint-1"
    assert record["baseline_primary"] == "Location"
    assert record["predicted_group"] == "Event"
    assert "Một sự kiện lịch sử." in record["evidence_sentences"]
    assert all(record[name] == "" for name in ["human_primary", "human_abstain", "review_status", "reviewer", "review_date", "notes"])
    json_record = json.loads(paths["review_jsonl"].read_text(encoding="utf-8"))
    assert json_record["title"] == '=HYPERLINK("bad")'
    assert json_record["review_status"] == ""
    assert json_record["human_primary"] == ""
    disagreement = json.loads(paths["disagreements_jsonl"].read_text(encoding="utf-8").splitlines()[0])
    assert disagreement["evidence_sentences"] == [{"id": "s1", "text": "Một sự kiện lịch sử."}]
    assert disagreement["provenance"] == {"source": "archive", "reference": "fixture-source"}
    assert disagreement["focus_groups"] == ["Event", "Location"]
    with pytest.raises(FileExistsError):
        evaluation.export_review(rows, results, tmp_path / "review")
    assert (rows, results) == original


def test_markdown_report_states_limits_and_never_invents_accuracy(tmp_path):
    metrics = evaluation.evaluate_records([row(1, "Event")], [result(1)])
    path = tmp_path / "report.md"
    evaluation.render_report(metrics, path)
    report = path.read_text(encoding="utf-8")
    assert "unassessed" in report
    assert "Baseline agreement" in report
    assert "not accuracy" in report
    assert "ontology" in report
    assert "human" in report.lower()


def test_review_template_can_be_loaded_as_pending_and_csv_review_can_be_imported(tmp_path):
    rows, results = [row(1, "Location")], [result(1)]
    paths = evaluation.export_review(rows, results, tmp_path / "review")
    pending = [json.loads(line) for line in paths["review_jsonl"].read_text(encoding="utf-8").splitlines()]
    assert evaluation.evaluate_records(rows, results, pending)["human_review"]["metrics"] is None
    with paths["review_csv"].open(encoding="utf-8", newline="") as handle:
        label = next(csv.DictReader(handle))
    label.update(human_primary="Event", human_abstain="false", review_status="reviewed",
                 reviewer="human-reader", review_date="2026-10-29")
    assert evaluation.evaluate_records(rows, results, [label])["human_review"]["metrics"]["blind_groq"]["accuracy"] == 1.0


def test_missing_mode_and_noncompleted_baseline_are_never_verified_evaluation_controls():
    prediction = result(1)
    del prediction["mode"]
    metrics = evaluation.evaluate_records([row(1, "Location", outcome="unmapped")], [prediction])
    assert metrics["views"]["baseline_verified_only"]["assigned_count"] == 0
    assert metrics["views"]["blind_groq"]["assigned_count"] == 0
    assert metrics["views"]["wikidata_first"]["group_counts"] == {"Event": 1}
    assert metrics["baseline_agreement"]["denominator"] == 0


def test_duplicate_human_rows_rejected_even_when_one_is_pending():
    pending = reviewed(1, "Location")
    pending["review_status"] = "pending"
    with pytest.raises(ValueError, match="duplicate"):
        evaluation.evaluate_records([row(1, "Event")], [result(1)], [pending, reviewed(1, "Event")])


@pytest.mark.parametrize("dangerous", ["\ttext", "\rtext", "\ntext", "   @SUM(1,1)", "+1", "-1"])
def test_csv_guards_control_characters_and_spreadsheet_formulas(tmp_path, dangerous):
    paths = evaluation.export_review([row(1, "Event", title=dangerous)], [result(1)], tmp_path)
    with paths["review_csv"].open(encoding="utf-8", newline="") as handle:
        record = next(csv.DictReader(handle))
    assert record["title"] == "'" + dangerous


def test_empty_dataset_has_explicit_undefined_rates_and_report(tmp_path):
    metrics = evaluation.evaluate_records([], [])
    assert metrics["baseline_agreement"]["rate"] is None
    assert metrics["operations"]["invalid_rate"] is None
    assert metrics["views"]["blind_groq"]["coverage"] is None
    evaluation.render_report(metrics, tmp_path / "report.md")


def test_markdown_report_refuses_to_overwrite_existing_file(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("Preserved historical report.\n", encoding="utf-8")
    metrics = evaluation.evaluate_records([row(1, "Event")], [result(1)])
    with pytest.raises(FileExistsError):
        evaluation.render_report(metrics, path)
    assert path.read_text(encoding="utf-8") == "Preserved historical report.\n"
