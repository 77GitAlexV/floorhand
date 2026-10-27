"""The quality metric must expose false confidence, omissions and extra orders."""
from collections import Counter
import json
import os

import pytest

from floorhand import domain, eval_intake as evaluation, intake, models, seed


def case(**changes):
    return {"id": "test", "lang": "en", "kind": "chat", "today": "2026-10-27",
            "source": "Home & Co: 2 Cozy armchairs", "gold": [{"product": "Cozy armchair",
            "qty": 2, "customer": "Home & Co", "due_date": None, "fabric": None, "frame": None}], **changes}


def item(**changes):
    f = {key: {"id": value, "value": value, "status": "ok" if value is not None else "empty"}
         for key, value in {"product": "Cozy armchair", "qty": 2, "customer": "Home & Co",
                            "due_date": None, "variant": None}.items()}
    for key, value in changes.items():
        f[key].update(value)
    return {"fields": f}


def test_metric_distinguishes_question_wrong_and_missing():
    counts, errors = evaluation.score_case(case(), {"items": [item(
        qty={"value": 9}, product={"status": "check", "value": "Wrong suggested label"},
        customer={"status": "empty", "value": ""})]})
    assert counts["qty"]["silent_error"] == 1
    assert counts["product"]["asked"] == 1
    assert counts["customer"]["missed"] == 1
    assert evaluation.totals(counts) == Counter(correct=3, asked=1, silent_error=1, missed=1)
    assert errors == [{"id": "test", "order": 0, "field": "qty", "expected": 2,
                       "actual": 9, "extra_order": False}]


def test_missing_order_including_null_fields_is_counted():
    counts, errors = evaluation.score_case(case(), {"items": []})
    assert evaluation.totals(counts) == Counter(missed=6)
    assert errors == []


def test_extra_order_false_assertions_are_silent_errors():
    counts, errors = evaluation.score_case(case(), {"items": [item(), item()]})
    assert evaluation.totals(counts) == Counter(correct=6, silent_error=3, missed=3)
    assert len(errors) == 3
    assert all(e["extra_order"] for e in errors)


def test_alignment_handles_reordered_duplicate_products():
    first = case()["gold"][0]
    second = {**first, "qty": 3, "due_date": "2026-11-05"}
    counts, errors = evaluation.score_case(case(gold=[first, second]), {"items": [
        item(qty={"value": 3}, due_date={"status": "ok", "value": "2026-11-05"}), item()]})
    assert evaluation.totals(counts) == Counter(correct=12)
    assert errors == []


def test_variant_status_applies_to_both_components():
    gold = {**case()["gold"][0], "fabric": "velvet beige", "frame": "oak"}
    counts, _ = evaluation.score_case(case(gold=[gold]), {"items": [item(
        variant={"id": None, "value": "velvet", "status": "check"})]})
    assert counts["fabric"]["asked"] == counts["frame"]["asked"] == 1
    counts, errors = evaluation.score_case(case(gold=[gold]), {"items": [item(
        variant={"id": "oak", "value": "oak", "status": "ok"})]})
    assert counts["fabric"]["silent_error"] == 1
    assert errors[0]["actual"] is None


def test_stock_and_optional_nulls_are_correct():
    gold = {k: v for k, v in case()["gold"][0].items() if k != "customer"}
    gold["stock"] = True
    counts, errors = evaluation.score_case(case(gold=[gold]), {"items": [item(
        customer={"id": intake.STOCK, "value": "to stock"})]})
    assert evaluation.totals(counts) == Counter(correct=6) and errors == []


def test_corpus_coverage_and_catalog():
    cases = evaluation.load_cases()
    assert len(cases) >= 60
    coverage = Counter((c["lang"], c["kind"]) for c in cases)
    assert all(coverage[lang, kind] >= 10 for lang in ("en", "ru") for kind in ("email", "chat", "table"))
    assert all(c["today"] == "2026-10-27" for c in cases)
    assert {o["product"] for c in cases for o in c["gold"]} == {p[0] for p in seed.PRODUCTS}


@pytest.mark.parametrize("change", [{"product": "Unknown invented product"}, {"fabric": "Unknown cloth"},
                                    {"frame": "Unknown frame"}, {"qty": 0}, {"stock": True}])
def test_invalid_gold_is_rejected(tmp_path, change):
    c = case()
    c["gold"][0].update(change)
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(c), encoding="utf-8")
    with pytest.raises(ValueError):
        evaluation.load_cases(path)


def test_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "duplicate.jsonl"
    path.write_text((json.dumps(case()) + "\n") * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        evaluation.load_cases(path)


def test_isolation_dates_and_cumulative_model_budget(monkeypatch):
    spent = []
    original_today = domain.today

    def fake_propose(conn, person, source):
        assert domain.today(conn) == "2026-10-27"
        assert conn.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == seed.N_ORDERS
        assert conn.execute("SELECT role FROM people WHERE id=?", (person,)).fetchone()[0] == "manager"
        spent.append(models.spent_micro(conn))
        models._log(conn, models.SPARK, "intake", cost_micro=100, estimated=True, ok=True)
        conn.execute("INSERT INTO synonyms VALUES ('product', 'test', 1, '2026-10-27')")
        conn.commit()
        return {"items": [item()], "model": models.SPARK}

    monkeypatch.setattr(intake, "propose", fake_propose)
    result = evaluation.evaluate([case(), case(id="other")], model="spark")
    assert spent == [0, 100]
    assert domain.today is original_today
    assert result["failures"] == []
    assert result["models"] == {models.SPARK: 2}


def test_offline_forces_no_network_and_restores_environment(monkeypatch):
    monkeypatch.setenv("FLOORHAND_META_LIVE", "1")
    monkeypatch.setenv("MODEL_API_KEY", "invented-test-key")
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline evaluation must not send anything")
    monkeypatch.setattr(models, "_post", forbidden)
    result = evaluation.evaluate([case()], model="offline")
    assert result["failures"] == []
    assert result["models"] == {"offline-rules": 1}
    assert os.environ["FLOORHAND_META_LIVE"] == "1"


def test_parser_crash_counts_as_missing_and_is_visible(monkeypatch):
    def broken(*args):
        raise ValueError("invented failure")
    monkeypatch.setattr(intake, "propose", broken)
    result = evaluation.evaluate([case()])
    assert evaluation.totals(result["groups"]["overall"])["missed"] == 6
    assert result["failures"] == [{"id": "test", "error": "ValueError: invented failure"}]
    assert "invented failure" in evaluation.markdown(result)


def test_malformed_proposal_does_not_abort_the_report(monkeypatch):
    monkeypatch.setattr(intake, "propose", lambda *args: {"items": [{}], "model": "test"})
    result = evaluation.evaluate([case()])
    assert len(result["failures"]) == 1
    assert evaluation.totals(result["groups"]["overall"])["missed"] == 6


def test_corpus_regression_and_bilingual_report(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOORHAND_META_LIVE", "0")
    report = tmp_path / "reports" / "eval.md"
    assert evaluation.main(["--report", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    assert "**0.00%**" in text
    assert all(f"kind:{kind}" in text for kind in ("email", "chat", "table"))
    assert "lang:en" in text and "lang:ru" in text
    assert "Пропуск" in text and "Вопрос" in text
    assert "CALC" in text and "FACT" in text
    # A low silent rate alone could hide a parser that asks or misses everything.
    result = evaluation.evaluate(evaluation.load_cases())
    counts = evaluation.totals(result["groups"]["overall"])
    assert result["failures"] == []
    assert counts["missed"] == 0
    assert counts["correct"] / sum(counts.values()) >= .90


@pytest.mark.parametrize("silent,failed,want", [(0, False, 0), (1, False, 1), (0, True, 1)])
def test_cli_exit_status_exposes_regressions(monkeypatch, tmp_path, silent, failed, want):
    counts = {k: Counter(correct=1, asked=0, silent_error=0, missed=0) for k in evaluation.FIELDS}
    counts["qty"]["silent_error"] = silent
    result = {"cases": 1, "orders": 1, "requested_model": "offline", "models": {"offline-rules": 1},
              "groups": {"overall": counts}, "silent_errors": [],
              "failures": [{"id": "test", "error": "test"}] if failed else []}
    monkeypatch.setattr(evaluation, "evaluate", lambda *args: result)
    assert evaluation.main(["--report", str(tmp_path / "report.md")]) == want
