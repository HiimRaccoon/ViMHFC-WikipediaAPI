"""Evaluate isolated suggestions without modifying corpus classification.

Human labels are explicit review artifacts, never copied from either classifier.
All rates describe the supplied sample, not the full pilot or corpus.
"""
from collections import Counter, defaultdict
import csv
from datetime import date, datetime
import io
import json
import math
from pathlib import Path
from statistics import mean, median

from vimfc.domain import GROUPS


_ABSTAIN = "__abstain__"
_UNRESOLVED = "__unresolved__"
_VIEWS = ("baseline_verified_only", "blind_groq", "wikidata_first")
_STATUSES = {"valid", "invalid_json", "invalid_evidence", "invalid_schema",
             "provider_error", "unattempted", "skipped_verified"}
_INVALID = {"invalid_json", "invalid_evidence", "invalid_schema"}
_FOCUS = {"Event", "Location", "Science / Technology"}


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _identity(record, rows):
    page_id = record.get("page_id")
    # CSV import preserves identifiers as decimal strings; booleans are not IDs.
    if isinstance(page_id, str) and page_id.isdecimal():
        page_id = int(page_id)
    if type(page_id) is not int or page_id not in rows:
        raise ValueError("unknown or invalid page_id")
    if record.get("input_fingerprint") != rows[page_id]["input_fingerprint"]:
        raise ValueError("input_fingerprint does not match the source record")
    return page_id


def _index(dataset_rows, results):
    rows = {}
    for row in dataset_rows:
        page_id = row["evaluation"]["page_id"]
        if type(page_id) is not int or page_id in rows:
            raise ValueError("invalid or duplicate dataset page_id")
        if not isinstance(row.get("input_fingerprint"), str) or not row["input_fingerprint"]:
            raise ValueError("missing input_fingerprint")
        if row["evaluation"].get("baseline_primary") not in (*GROUPS, None):
            raise ValueError("invalid baseline primary group")
        sentences = row["input"]["sentences"]
        ids = [(type(sentence["id"]), sentence["id"]) for sentence in sentences]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate source sentence ID")
        rows[page_id] = row
    indexed = {}
    for result in results:
        page_id = _identity(result, rows)
        if page_id in indexed:
            raise ValueError("duplicate result for a source record; evaluate model runs separately")
        if result.get("status") not in _STATUSES:
            raise ValueError("unknown result status")
        if result.get("mode") not in {None, "blind", "assisted"}:
            raise ValueError("unknown inference mode")
        if result["status"] == "valid":
            prediction = result.get("prediction")
            if not isinstance(prediction, dict) or type(prediction.get("abstain")) is not bool:
                raise ValueError("invalid valid-status prediction")
            group = prediction.get("predicted_group")
            abstain = prediction["abstain"]
            if (abstain and group is not None) or (not abstain and group not in GROUPS):
                raise ValueError("inconsistent predicted group and abstention")
            evidence = prediction.get("evidence_sentence_ids")
            source_ids = {(type(s["id"]), s["id"]) for s in rows[page_id]["input"]["sentences"]}
            if not isinstance(evidence, list) or (not abstain and not evidence):
                raise ValueError("missing evidence sentence IDs")
            if any(type(sid) not in (str, int) or (type(sid), sid) not in source_ids for sid in evidence):
                raise ValueError("evidence sentence ID does not exist in source")
            if len({(type(sid), sid) for sid in evidence}) != len(evidence):
                raise ValueError("duplicate evidence sentence IDs")
        indexed[page_id] = result
    return rows, indexed


def _human_gold(labels, rows):
    gold = {}
    seen = set()
    for label in labels or []:
        page_id = _identity(label, rows)
        if page_id in seen:
            raise ValueError("duplicate human review row")
        seen.add(page_id)
        if label.get("review_status") != "reviewed":
            continue
        if not isinstance(label.get("reviewer"), str) or not label["reviewer"].strip():
            raise ValueError("reviewed label requires a reviewer")
        review_date = label.get("review_date")
        if not isinstance(review_date, str) or not review_date.strip():
            raise ValueError("reviewed label requires a review_date")
        try:
            if "T" in review_date:
                datetime.fromisoformat(review_date.replace("Z", "+00:00"))
            else:
                date.fromisoformat(review_date)
        except ValueError as exc:
            raise ValueError("invalid review_date") from exc
        abstain = label.get("human_abstain")
        if isinstance(abstain, str):
            abstain = {"true": True, "false": False}.get(abstain.lower())
        if type(abstain) is not bool:
            raise ValueError("human_abstain must explicitly be true or false")
        group = label.get("human_primary") or None
        if (abstain and group is not None) or (not abstain and group not in GROUPS):
            raise ValueError("inconsistent or invalid human primary group")
        gold[page_id] = _ABSTAIN if abstain else group
    return gold


def _predictions(rows, results):
    views = {name: {} for name in _VIEWS}
    for page_id, row in rows.items():
        evaluation = row["evaluation"]
        baseline = evaluation.get("baseline_primary") if evaluation["baseline_outcome"] == "completed" else None
        result = results.get(page_id, {})
        suggested = None
        if result.get("status") == "valid":
            prediction = result["prediction"]
            suggested = _ABSTAIN if prediction["abstain"] else prediction["predicted_group"]
        views["baseline_verified_only"][page_id] = baseline
        # Missing mode is never assumed to be evidence of a blind request.
        views["blind_groq"][page_id] = suggested if result.get("mode") == "blind" else None
        views["wikidata_first"][page_id] = baseline or suggested
    return views


def _coverage(predictions, ids):
    values = [predictions[page_id] for page_id in ids]
    assigned = sum(value in GROUPS for value in values)
    abstained = values.count(_ABSTAIN)
    return {"denominator": len(values), "assigned_count": assigned,
            "coverage": _ratio(assigned, len(values)), "abstention_count": abstained,
            "abstention_rate": _ratio(abstained, len(values)),
            "unresolved_count": values.count(None),
            "group_counts": dict(sorted(Counter(v for v in values if v in GROUPS).items()))}


def _gold_metrics(predictions, gold):
    confusion = defaultdict(Counter)
    for page_id, truth in gold.items():
        confusion[truth][predictions[page_id] or _UNRESOLVED] += 1
    present = sorted(set(gold.values()) | {predictions[i] or _UNRESOLVED for i in gold})
    per_group = {}
    for group in present:
        tp = confusion[group][group]
        support = sum(confusion[group].values())
        predicted_count = sum(counts[group] for counts in confusion.values())
        precision = tp / predicted_count if predicted_count else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_group[group] = {"support": support, "predicted_count": predicted_count,
                            "precision": precision, "recall": recall, "f1": f1,
                            "accuracy": _ratio(tp, support),
                            "coverage": _ratio(sum(n for p, n in confusion[group].items() if p in GROUPS), support)}
    correct = sum(predictions[page_id] == truth for page_id, truth in gold.items())
    return {"denominator": len(gold), "accuracy": _ratio(correct, len(gold)),
            "precision": mean(v["precision"] for v in per_group.values()),
            "recall": mean(v["recall"] for v in per_group.values()),
            "macro_f1": mean(v["f1"] for v in per_group.values()),
            "macro_labels": present, "per_group": per_group,
            "confusion_matrix": {truth: {p: n for p, n in sorted(counts.items()) if n}
                                 for truth, counts in sorted(confusion.items()) if sum(counts.values())},
            "coverage": _ratio(sum(predictions[i] in GROUPS for i in gold), len(gold))}


def _operations(rows, results):
    statuses = Counter(results.get(i, {}).get("status", "unattempted") for i in rows)
    requested = [result for result in results.values()
                 if result["status"] not in {"unattempted", "skipped_verified"}]
    latencies = []
    usage = dict.fromkeys(("prompt_tokens", "completion_tokens", "total_tokens"), 0)
    for result in requested:
        latency = result.get("latency_seconds")
        if latency is not None:
            if type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0:
                raise ValueError("invalid latency")
            latencies.append(latency)
        for key in usage:
            count = (result.get("usage") or {}).get(key, 0)
            if type(count) is not int or count < 0:
                raise ValueError("invalid token usage")
            usage[key] += count
    denominator = len(requested)
    return {"requested_records": denominator, "request_attempt_count": None,
            "attempt_count_note": "Actual HTTP attempts, retries and reserved tokens require the durable attempt ledger; result rows are not HTTP call counts.",
            "status_counts": dict(sorted(statuses.items())),
            "invalid_rate_denominator": denominator,
            "invalid_rate": _ratio(sum(statuses[s] for s in _INVALID), denominator),
            "invalid_json_rate": _ratio(statuses["invalid_json"], denominator),
            "invalid_evidence_rate": _ratio(statuses["invalid_evidence"], denominator),
            "invalid_schema_rate": _ratio(statuses["invalid_schema"], denominator),
            "usage": usage,
            "latency_seconds": {"count": len(latencies), "mean": mean(latencies) if latencies else None,
                                "median": median(latencies) if latencies else None,
                                "max": max(latencies) if latencies else None,
                                "total": sum(latencies)}}


def evaluate_records(dataset_rows: list, results: list, human_labels: list | None = None) -> dict:
    """Evaluate one result per source; human labels must explicitly certify review."""
    rows, indexed = _index(dataset_rows, results)
    gold = _human_gold(human_labels, rows)
    views = _predictions(rows, indexed)
    comparable = [i for i in rows if views["baseline_verified_only"][i] in GROUPS
                  and views["blind_groq"][i] in GROUPS]
    agreed = sum(views["baseline_verified_only"][i] == views["blind_groq"][i] for i in comparable)
    metrics = {
        "dataset_count": len(rows),
        "views": {name: _coverage(predictions, rows) for name, predictions in views.items()},
        "baseline_agreement": {"count": agreed, "denominator": len(comparable), "rate": _ratio(agreed, len(comparable))},
        "baseline_disagreement": {"count": len(comparable) - agreed, "denominator": len(comparable),
                                  "rate": _ratio(len(comparable) - agreed, len(comparable))},
        "human_review": {"status": "assessed" if gold else "unassessed", "reviewed_count": len(gold),
                         "metrics": {name: _gold_metrics(pred, gold) for name, pred in views.items()} if gold else None},
        "operations": _operations(rows, indexed),
        "policies": {"coverage_denominator": "All dataset records, including invalid, missing and skipped results.",
                     "baseline_agreement": "Only completed baseline groups with valid, non-abstaining predictions from actual blind requests; agreement is not accuracy.",
                     "human_metrics": "Only fingerprint-bound reviewed labels. Abstentions and missing predictions count as errors against a human group; explicit human abstention can match model abstention.",
                     "macro_average": "Unweighted mean over labels appearing in reviewed truth or predictions, including __abstain__ and __unresolved__ when present; zero division is zero.",
                     "evidence": "Sentence reference validity does not establish factual sufficiency or an ontology bug.",
                     "scope": "Sample-level descriptive metrics; stratified sampling does not estimate pilot-wide accuracy."},
    }
    for output, field in (("by_baseline_outcome", "baseline_outcome"),
                          ("by_baseline_group", "baseline_primary"),
                          ("by_focus_stratum", "sampling_stratum")):
        groups = defaultdict(list)
        for page_id, row in rows.items():
            groups[str(row["evaluation"].get(field) or "__none__")].append(page_id)
        metrics[output] = {}
        for group, ids in sorted(groups.items()):
            subset_gold = {i: gold[i] for i in ids if i in gold}
            metrics[output][group] = {"count": len(ids),
                "views": {name: _coverage(pred, ids) for name, pred in views.items()},
                "human_metrics": {name: _gold_metrics(pred, subset_gold) for name, pred in views.items()} if subset_gold else None}
    return metrics


def _review_records(rows, results):
    for page_id, row in rows.items():
        result = results.get(page_id, {})
        prediction = result.get("prediction") if result.get("status") == "valid" else None
        evidence_ids = prediction["evidence_sentence_ids"] if prediction else []
        evidence = [s for s in row["input"]["sentences"] if s["id"] in evidence_ids]
        yield {"page_id": page_id, "input_fingerprint": row["input_fingerprint"],
               "revision_id": row["evaluation"].get("revision_id"), "snapshot": row["evaluation"].get("snapshot"),
               "title": row["input"]["title"], "lead_text": row["input"]["lead_text"],
               "baseline_outcome": row["evaluation"]["baseline_outcome"],
               "baseline_primary": row["evaluation"].get("baseline_primary"),
               "classification_reason": row["evaluation"].get("classification_reason"),
               "sampling_stratum": row["evaluation"].get("sampling_stratum"),
               "model_id": result.get("model_id"), "mode": result.get("mode"),
               "status": result.get("status", "unattempted"),
               "predicted_group": prediction["predicted_group"] if prediction else None,
               "abstain": prediction["abstain"] if prediction else None,
               "reason": prediction.get("reason") if prediction else None,
               "ambiguity": prediction.get("ambiguity") if prediction else None,
               "evidence_sentence_ids": evidence_ids, "evidence_sentences": evidence,
               "provenance": row.get("provenance", {}),
               "human_primary": "", "human_abstain": "", "review_status": "",
               "reviewer": "", "review_date": "", "notes": ""}


def _csv_cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value


def export_review(dataset_rows, results, output_dir) -> dict:
    """Create immutable review templates; edit a copy and keep identity fields intact.

    CSV cannot enforce read-only cells: input_fingerprint and page_id are binding
    source fields and imports reject any edits to them. JSONL preserves exact text.
    """
    rows, indexed = _index(dataset_rows, results)
    records = list(_review_records(rows, indexed))
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {"review_csv": root / "human-review.csv", "review_jsonl": root / "human-review.jsonl",
             "disagreements_jsonl": root / "disagreements.jsonl"}
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("review export already exists; use a new output directory")
    stream = io.StringIO(newline="")
    if records:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows({key: _csv_cell(value) for key, value in record.items()} for record in records)
    disagreements = []
    for record in records:
        if record["baseline_outcome"] == "completed" and record["baseline_primary"] in GROUPS and record["predicted_group"] in GROUPS and record["baseline_primary"] != record["predicted_group"]:
            disagreements.append({**record, "focus_groups": sorted({record["baseline_primary"], record["predicted_group"]} & _FOCUS),
                                  "interpretation": "Review candidate only; a suggestion is not proof of an ontology bug."})
    content = {"review_csv": stream.getvalue(),
               "review_jsonl": "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
               "disagreements_jsonl": "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in disagreements)}
    for key, path in paths.items():
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(content[key])
    return paths


def render_report(metrics, output_path):
    """Write a concise Markdown report without creating ground-truth claims."""
    lines = ["# Groq classification evaluation", "", f"Dataset records: {metrics['dataset_count']}.", "",
             "| View | Assigned | Coverage | Abstained | Unresolved |", "| --- | ---: | ---: | ---: | ---: |"]
    for name, view in metrics["views"].items():
        coverage = f"{view['coverage']:.2%}" if view["coverage"] is not None else "unassessed"
        lines.append(f"| {name} | {view['assigned_count']} | {coverage} | {view['abstention_count']} | {view['unresolved_count']} |")
    agreement = metrics["baseline_agreement"]
    lines.extend(["", f"Baseline agreement: {agreement['count']}/{agreement['denominator']} comparable blind predictions. Agreement is not accuracy.",
                  "", f"Human review: {metrics['human_review']['status']} ({metrics['human_review']['reviewed_count']} reviewed records)."])
    if metrics["human_review"]["metrics"] is None:
        lines.append("Human accuracy, precision, recall, confusion matrices and Macro-F1 are unassessed; no verified human labels were supplied.")
    else:
        lines.extend(["", "| View | Human accuracy | Macro-F1 |", "| --- | ---: | ---: |"])
        for name, values in metrics["human_review"]["metrics"].items():
            lines.append(f"| {name} | {values['accuracy']:.4f} | {values['macro_f1']:.4f} |")
    lines.extend(["", "Evidence and interpretation limits:", ""])
    lines.extend(f"- {policy}" for policy in metrics["policies"].values())
    lines.extend(["", "Review disagreements in Event, Location and Science / Technology against the historical sentences and provenance. Possible missing ontology rules, precedence ambiguity, historical Wikidata gaps, or misleading category hints are hypotheses requiring independent review.",
                  "", "Operational and per-stratum details:", "", "```json",
                  json.dumps({key: metrics[key] for key in ("operations", "by_baseline_outcome", "by_baseline_group", "by_focus_stratum")}, ensure_ascii=False, indent=2, allow_nan=False), "```", ""])
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return output
