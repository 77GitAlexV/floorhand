"""Reproducible intake evaluation, using fictional messages and isolated seeded databases."""
import argparse
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
import json
import os
from pathlib import Path
import random
import tempfile
from unittest.mock import patch

from . import db, domain, intake, seed

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "eval" / "intake_cases.jsonl"
FIELDS = ("product", "qty", "customer", "due_date", "fabric", "frame")
OUTCOMES = ("correct", "asked", "silent_error", "missed")


def load_cases(path=CASES):
    cases = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    seen = set()
    for case in cases:
        if case["id"] in seen or case["lang"] not in ("en", "ru") or case["kind"] not in (
                "email", "chat", "table"):
            raise ValueError(f"Invalid or duplicate case: {case['id']}")
        seen.add(case["id"])
        if not isinstance(case["source"], str) or not case["source"].strip() or not case["gold"]:
            raise ValueError(f"Empty case: {case['id']}")
        date.fromisoformat(case["today"])
        for order in case["gold"]:
            if not all(k in order for k in ("product", "qty", "due_date", "fabric", "frame")):
                raise ValueError(f"Incomplete gold: {case['id']}")
            if ("customer" in order) == ("stock" in order):
                raise ValueError(f"Choose customer or stock: {case['id']}")
            if order["product"] not in {p[0] for p in seed.PRODUCTS}:
                raise ValueError(f"Product outside demo catalog: {case['id']}")
            if any(order[k] is not None and order[k] not in catalog
                   for k, catalog in (("fabric", seed.FABRICS), ("frame", seed.FRAMES))):
                raise ValueError(f"Variant outside demo catalog: {case['id']}")
            if order["qty"] is not None and (type(order["qty"]) is not int or not 0 < order["qty"] <= 500):
                raise ValueError(f"Invalid gold quantity: {case['id']}")
            if "stock" in order and order["stock"] is not True:
                raise ValueError(f"Invalid stock flag: {case['id']}")
            if order["due_date"] is not None:
                date.fromisoformat(order["due_date"])
    return cases


def expected(order):
    return {**order, "customer": intake.STOCK if order.get("stock") else order.get("customer")}


def observed(item):
    """Keep statuses: a displayed guess with status check is a question, never a fact."""
    fields = item["fields"]
    result = {}
    for key in FIELDS[:4]:
        f = fields[key]
        value = intake.STOCK if key == "customer" and f["id"] == intake.STOCK else f["value"]
        result[key] = (value if f["status"] == "ok" else None, f["status"])
    variant = fields["variant"]
    parts = (variant.get("id") or "").split(" · ")
    for key, catalog in (("fabric", seed.FABRICS), ("frame", seed.FRAMES)):
        result[key] = (next((p for p in parts if p in catalog), None), variant["status"])
    return result


def _same(a, b):
    if a in (None, "") and b in (None, ""):
        return True
    return str(a).strip().casefold() == str(b).strip().casefold()


def align(gold, predictions):
    """Minimum-cost one-to-one alignment; input order must not affect the score.

    A product match dominates the other fields. Equal-sized unrelated orders still
    compare as wrong, rather than disguising false assertions as missing orders.
    """
    size = max(len(gold), len(predictions))
    if size > 16:
        raise ValueError("Split evaluation cases into at most 16 orders.")

    def cost(i, j):
        if i >= len(gold) or j >= len(predictions):
            return 30
        g, p = expected(gold[i]), predictions[j]
        return sum((20 if k == "product" else 1) * (not _same(g.get(k), p[k][0]))
                   for k in FIELDS)

    @lru_cache(None)
    def solve(i, mask):
        if i == size:
            return 0, ()
        choices = []
        for j in range(size):
            if not mask & (1 << j):
                tail, indices = solve(i + 1, mask | (1 << j))
                choices.append((cost(i, j) + tail, (j,) + indices))
        return min(choices)

    return [(i if i < len(gold) else None, j if j < len(predictions) else None)
            for i, j in enumerate(solve(0, 0)[1])]


def score_case(case, proposal):
    predictions = [observed(item) for item in proposal["items"]]
    counts = {k: Counter({outcome: 0 for outcome in OUTCOMES}) for k in FIELDS}
    errors = []
    for i, j in align(case["gold"], predictions):
        gold = expected(case["gold"][i]) if i is not None else None
        for key in FIELDS:
            want = gold.get(key) if gold else None
            value, status = predictions[j][key] if j is not None else (None, "missing")
            if j is None:
                outcome = "missed"
            elif i is None:
                # An extra proposed order has no legitimate fields, even if it asks.
                outcome = "silent_error" if status == "ok" else "missed"
            elif status == "check":
                outcome = "asked"
            elif status == "ok":
                outcome = "correct" if _same(value, want) else "silent_error"
            else:
                outcome = "correct" if want is None else "missed"
            counts[key][outcome] += 1
            if outcome == "silent_error":
                errors.append({"id": case["id"], "order": i, "field": key,
                               "expected": want, "actual": value, "extra_order": i is None})
    return counts, errors


def evaluate(cases, model="offline"):
    groups = defaultdict(lambda: {k: Counter({o: 0 for o in OUTCOMES}) for k in FIELDS})
    errors, failures, used = [], [], Counter()
    # Clone a seed, rather than letting an earlier proposal or learned synonym leak.
    # Keep model-call history across clones so Spark's budget covers the entire run.
    ledger = []
    with tempfile.TemporaryDirectory(prefix=".intake-eval-", dir=ROOT) as folder:
        template = db.connect(Path(folder) / "seed.db")
        try:
            seed.build(template, random.Random(26), today=date(2026, 10, 27))
            for case in cases:
                conn = db.connect(Path(folder) / "case.db")
                try:
                    template.backup(conn)
                    if ledger:
                        conn.executemany("INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?)", ledger)
                        conn.commit()
                    person = conn.execute("SELECT id FROM people WHERE role='manager'").fetchone()[0]
                    env = {"FLOORHAND_META_LIVE": "0"} if model == "offline" else {}
                    with patch.dict(os.environ, env), patch.object(domain, "today", return_value=case["today"]):
                        try:
                            proposal = intake.propose(conn, person, case["source"])
                            counts, wrong = score_case(case, proposal)
                        except Exception as exc:
                            # A crashed case remains in the denominator, and fails the CLI.
                            failures.append({"id": case["id"], "error": f"{type(exc).__name__}: {exc}"})
                            proposal = {"items": [], "model": "failed"}
                            counts, wrong = score_case(case, proposal)
                    ledger = [tuple(r) for r in conn.execute("SELECT * FROM model_calls ORDER BY id")]
                    used[proposal["model"]] += 1
                    errors.extend(wrong)
                    for group in ("overall", "kind:" + case["kind"], "lang:" + case["lang"]):
                        for key in FIELDS:
                            groups[group][key].update(counts[key])
                finally:
                    conn.close()
        finally:
            template.close()
    return {"cases": len(cases), "orders": sum(len(c["gold"]) for c in cases),
            "requested_model": model, "models": dict(used), "groups": dict(groups),
            "silent_errors": errors, "failures": failures}


def totals(fields):
    return sum(fields.values(), Counter())


def silent_rate(result):
    total = totals(result["groups"]["overall"])
    return total["silent_error"] / max(1, sum(total.values()))


def markdown(result):
    total = totals(result["groups"]["overall"])
    lines = ["# Intake quality / Качество приёма заказов", "",
             f"FACT / ФАКТ: {result['cases']} cases / примеров; {result['orders']} gold orders / эталонных заказов.",
             f"Requested / Запрошено: `{result['requested_model']}`. Actual / Фактически: `{result['models']}`.", "",
             "CALC / РАСЧЁТ: each aligned order contributes six fields; extra orders also count. "
             "Silent rate = wrong fields with status ok / all compared fields (target ≤ 1%). "
             "Asked = status check, not an error. Missed = absent field/order. "
             "Empty optional fields are correct only when gold is null. "
             "Orders are aligned one-to-one by minimum cost, preferring product matches.", "",
             "Каждый заказ даёт шесть полей; лишние заказы тоже учитываются. "
             "Тихая ошибка — неверное поле со статусом ok. Вопрос (check) не ошибка. "
             "Пропуск — отсутствующее поле или заказ. Пустое необязательное поле верно только при null в эталоне.", "",
             f"CALC / РАСЧЁТ: silent error rate / доля тихих ошибок: **{silent_rate(result):.2%}**.", ""]
    lines += [f"CALC / РАСЧЁТ: correct / верно **{total['correct'] / max(1, sum(total.values())):.2%}**; "
              f"asked / вопросов **{total['asked']}**; missed / пропусков **{total['missed']}**.", "",
              "This is a fixed development corpus, not an independent generalization benchmark. "
              "Weekdays mean the upcoming occurrence; next Friday on Friday means the following week. "
              "Unknown customers and intentionally incomplete or ambiguous fields should ask.", "",
              "Это фиксированный набор для разработки, а не независимая оценка обобщения. "
              "Дни недели означают ближайшее наступление; «в следующую пятницу» в пятницу — через неделю. "
              "Неизвестный клиент и неоднозначное поле требуют вопроса.", ""]
    for group, fields in result["groups"].items():
        group_total = totals(fields)
        lines += [f"## {group}", "", "CALC / РАСЧЁТ", "",
                  f"Silent / Тихие: {group_total['silent_error'] / max(1, sum(group_total.values())):.2%}.", "",
                  "| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |",
                  "|---|---:|---:|---:|---:|"]
        for key, counts in [*fields.items(), ("TOTAL / ВСЕГО", totals(fields))]:
            lines.append(f"| {key} | " + " | ".join(str(counts[o]) for o in OUTCOMES) + " |")
        lines.append("")
    lines += ["## Silent errors / Тихие ошибки", ""]
    if not result["silent_errors"]:
        lines.append("None / Нет.")
    for error in result["silent_errors"]:
        lines.append(f"- `{error['id']}` ({error['order']}): {error['field']}: "
                     f"expected / ожидалось `{error['expected']}`, got / получено `{error['actual']}`"
                     + (" (extra order / лишний заказ)" if error["extra_order"] else ""))
    lines += ["", "## Failures / Сбои", ""]
    lines.extend(f"- `{f['id']}`: {f['error']}" for f in result["failures"])
    if not result["failures"]:
        lines.append("None / Нет.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Intake quality / Качество приёма заказов")
    parser.add_argument("--model", choices=("offline", "spark"), default="offline")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--report", type=Path, default=ROOT / "reports" / "intake-eval.md")
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)
    if not cases:
        parser.error("No cases / Нет примеров")
    result = evaluate(cases, args.model)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(markdown(result), encoding="utf-8")
    counts = totals(result["groups"]["overall"])
    summary = {k: counts[k] for k in OUTCOMES}
    print(f"CALC / РАСЧЁТ: {summary}; silent / тихие {silent_rate(result):.2%}")
    print(f"Report / Отчёт: {args.report}")
    return int(bool(result["failures"]) or silent_rate(result) > .01)


if __name__ == "__main__":
    raise SystemExit(main())
