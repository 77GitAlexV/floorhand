"""Safety regressions for multilingual intake, separate from the original API tests."""
from datetime import date
import random

import pytest

from floorhand import db, domain, intake, seed

TODAY = date(2026, 10, 27)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOORHAND_META_LIVE", "0")
    monkeypatch.setattr(domain, "today", lambda conn: TODAY.isoformat())
    c = db.connect(tmp_path / "rules.db")
    seed.build(c, random.Random(26), today=TODAY)
    yield c
    c.close()


def propose(conn, source):
    person = conn.execute("SELECT id FROM people WHERE role='manager'").fetchone()[0]
    return intake.propose(conn, person, source)["items"]


@pytest.mark.parametrize("text,want", [
    ("next Friday", "2026-10-30"), ("в пятницу", "2026-10-30"),
    ("к следующему понедельнику", "2026-11-02"), ("tomorrow", "2026-10-28"),
    ("послезавтра", "2026-10-29"), ("today", "2026-10-27"), ("к 15-му", "2026-11-15"),
    ("к 30-му", "2026-10-30"), ("12 ноября", "2026-11-12"), ("12th November", "2026-11-12"),
    ("Nov 5, 2027", "2027-11-05"), ("5 ноября 2027", "2027-11-05"),
    ("05.11.2026", "2026-11-05"), ("31 февраля", None), ("2026-11-31", None),
    ("2026-11-01 or 2026-11-02", None), ("Nov 1 or 1 November", "2026-11-01"),
    ("not decided", None),
])
def test_dates(text, want):
    got = intake._parse_date(text, TODAY)
    assert (got.isoformat() if got else None) == want


def test_dates_cross_year_and_same_weekday():
    assert intake._parse_date("к 5-му", date(2026, 12, 27)) == date(2027, 1, 5)
    assert intake._parse_date("next Friday", date(2026, 10, 30)) == date(2026, 11, 6)
    assert intake._parse_date("Friday", date(2026, 10, 30)) == date(2026, 10, 30)
    assert intake._parse_date("29 February", date(2028, 12, 27)) is None


@pytest.mark.parametrize("text,amount", [
    ("две штуки кресла Кози", "2"), ("Бруно стулья 12 шт", "12"),
    ("a pair of Nordic lounge chairs", "2"), ("a Daybed Lina", "1"),
    ("Round pouf x12", "12"), ("3 pcs Round pouf", "3"),
    ("двадцать один пуф", "21"), ("twenty-one Round poufs", "21"),
    ("Sofa Oslo 3-seat due Nov 12", None), ("Диван Осло 2-местный к 15-му", None),
    ("Round pouf due 12 ноября", None), ("Cozy armchair 2 or 3", None),
    ("-2 Cozy armchairs", None), ("2 Cozy armchairs actually make it three", "3"),
    ("два кресла Кози, поправка: сделайте четыре", "4"),
    ("2 кресла Кози, нет, лучше 3", "3"),
])
def test_quantities(text, amount):
    value, quote = intake._qty(text)
    assert value == amount
    assert quote is None or quote in text


@pytest.mark.parametrize("ru,en", [
    ("два кресла Кози", "Cozy armchair"), ("три кресла Харбор", "Harbor armchair"),
    ("две качалки Харбор", "Harbor rocking chair"), ("кресло Нордик", "Nordic lounge chair"),
    ("стулья Бруно", "Bruno chair"), ("круглых пуфов", "Round pouf"),
    ("квадратных пуфа", "Square pouf"), ("подставок для ног", "Footstool"),
    ("диван Осло 3-местный", "Sofa Oslo 3-seat"), ("диван Осло 2-местный", "Sofa Oslo 2-seat"),
    ("кушетка Лина", "Daybed Lina"), ("детских кресла Банни", "Kids armchair Bunny"),
])
def test_russian_demo_products(conn, ru, en):
    cat = intake._catalog(conn)
    pid, _ = intake._match("product", ru, cat)
    assert cat["product"][pid] == en


def test_russian_orders_variants_and_local_metadata(conn):
    items = propose(conn, "Home & Co: два кресла Кози бежевый велюр орех завтра; "
                    "Urban Loft: три круглых пуфа молочное букле в пятницу")
    assert len(items) == 2
    for item, customer, day in zip(items, ("Home & Co", "Urban Loft"), ("2026-10-28", "2026-10-30")):
        assert item["ready"]
        assert item["fields"]["customer"]["value"] == customer
        assert item["fields"]["due_date"]["value"] == day
    assert items[0]["fields"]["variant"]["value"] == "velvet beige · walnut"
    assert items[1]["fields"]["qty"]["value"] == 3
    assert items[1]["fields"]["variant"]["value"] == "boucle milk"


def test_forwarded_history_does_not_become_another_order(conn):
    items = propose(conn, "Home & Co: 3 Bruno chairs by tomorrow\n----- Forwarded message -----\n"
                    "> Urban Loft: 8 Bruno chairs by Nov 7")
    assert len(items) == 1
    assert items[0]["fields"]["qty"]["value"] == 3
    assert items[0]["fields"]["due_date"]["value"] == "2026-10-28"


def test_forwarded_only_order_is_kept(conn):
    items = propose(conn, "FYI\n----- Forwarded message -----\n> Home & Co: 2 Cozy armchairs\n> due tomorrow")
    assert len(items) == 1 and items[0]["ready"]
    assert items[0]["fields"]["due_date"]["value"] == "2026-10-28"


@pytest.mark.parametrize("suffix", ["due 31 February", "due 2026-11-01 or 2026-11-02"])
def test_invalid_or_conflicting_dates_ask(conn, suffix):
    item = propose(conn, "Home & Co: 2 Cozy armchairs " + suffix)[0]
    assert item["fields"]["due_date"]["status"] == "check"
    assert not item["ready"]


def test_ambiguous_correction_asks_which_order(conn):
    items = propose(conn, "Home & Co: 2 Cozy armchairs and 4 Round poufs. Actually make it 3.")
    assert len(items) == 2
    assert all(i["fields"]["qty"]["status"] == "check" for i in items)


def test_shuffled_russian_table_words_and_separate_frame(conn):
    items = propose(conn, "Дата отгрузки\tОбивка\tШтук всего\tНаименование\tПокупатель\tНожки\n"
                    "в пятницу\tбежевый велюр\tдва\tКресло Кози\tHome & Co\tорех")
    assert len(items) == 1 and items[0]["ready"]
    assert items[0]["fields"]["variant"]["value"] == "velvet beige · walnut"
    assert items[0]["fields"]["qty"]["value"] == 2


def test_csv_quotes_and_multiline_cells_keep_source_evidence(conn):
    source = ('Product,Qty,Client,Fabric,Frame\r\n'
              '"Cozy armchair",two,Home & Co,"velvet beige\r\n",walnut\r\n')
    item = propose(conn, source)[0]
    assert item["ready"]
    assert item["fields"]["variant"]["quote"] in source
    assert item["fields"]["variant"]["value"] == "velvet beige · walnut"


@pytest.mark.parametrize("variant", ["velvet", "velvet beige oak walnut", "fabric: violet tweed",
                                          "frame: steel"])
def test_ambiguous_or_unknown_variant_is_a_question(conn, variant):
    item = propose(conn, "Home & Co: 2 Cozy armchairs, " + variant)[0]
    assert item["fields"]["variant"]["status"] == "check"
    assert not item["ready"]


def test_stock_table_and_unknown_customer(conn):
    item = propose(conn, "Товар;Кол-во;Клиент\nКруглый пуф;три;на склад")[0]
    assert item["fields"]["customer"]["id"] == intake.STOCK and item["ready"]
    unknown = propose(conn, "Клиент: Макет Лунная Полка; два кресла Кози")[0]
    customer = unknown["fields"]["customer"]
    assert customer["status"] == "check"
    assert any(o["id"] == "new:Макет Лунная Полка" for o in customer["options"])


def test_competing_complete_product_names_do_not_pick_longest(conn):
    item = propose(conn, "Home & Co: 2 Cozy armchairs or Nordic lounge chairs")[0]
    assert item["fields"]["product"]["status"] == "check"


def test_grounding_uses_quotes_instead_of_unsupported_model_values(conn):
    source = "two Round poufs for stock by tomorrow"
    raw = {"product": intake._field("Harbor armchair", "Round poufs"),
           "qty": intake._field(50, "two"),
           "customer": intake._field("Home & Co", "for stock"),
           "due_date": intake._field("2026-11-05", "tomorrow")}
    f = intake.ground(conn, source, raw, intake._catalog(conn), TODAY)["fields"]
    assert f["product"]["value"] == "Round pouf"
    assert f["qty"]["status"] == "check"
    assert f["customer"]["id"] == intake.STOCK
    assert f["due_date"]["value"] == "2026-10-28"


def test_no_quote_means_no_accepted_field(conn):
    raw = {key: intake._field(value, None) for key, value in {
        "product": "Harbor armchair", "qty": 2, "customer": "stock",
        "due_date": "2026-11-01", "variant": "velvet beige"}.items()}
    item = intake.ground(conn, "Hello", raw, intake._catalog(conn), TODAY)
    assert all(f["status"] != "ok" for f in item["fields"].values())


def test_empty_table_does_not_parse_headers_as_orders(conn):
    assert intake._extract_offline(conn, "Product;Qty;Customer\n;2;Home & Co") == []
