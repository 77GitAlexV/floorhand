"""Paper identities survive change; paper rendering never records a print."""
import inspect
import json
import random
import sqlite3
from datetime import date
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from floorhand import db, domain, seed
from floorhand.features import paperwork as p, paperwork_html as h


@pytest.fixture
def conn(tmp_path, monkeypatch):
    for name in ('HOST', 'FROM', 'USER', 'PASSWORD', 'TLS', 'PORT'):
        monkeypatch.delenv('FLOORHAND_SMTP_' + name, raising=False)
    c = db.connect(tmp_path / 'paper.db')
    seed.build(c, random.Random(1), today=date.today())
    yield c
    c.close()


def manager(c):
    return c.execute("SELECT id FROM people WHERE role='manager'").fetchone()[0]


def fresh(c, variant='fictional fabric'):
    oid = domain.create_order(c, product_id=1, qty=2, customer_id=1, due_date=domain.today(c), variant=variant, source='manual')
    return c.execute('SELECT id FROM units WHERE order_id=? ORDER BY seq', (oid,)).fetchone()[0]


def print_one(c, uid, **kw):
    return p.print_sheets(c, manager(c), scope='units', unit_ids=[uid], **kw)


def counts(c):
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {table: c.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}


def token(c, uid):
    return p.ensure_codes(c, [uid])[uid]


def test_permanent_tokens_and_append_only_paper(conn):
    uid = fresh(conn)
    first = token(conn, uid)
    assert len(first) == 8 and set(first) <= set(p.ALPHABET)
    assert token(conn, uid) == first
    print_one(conn, uid)
    print_one(conn, uid, reason='lost')
    p.void_sheet(conn, manager(conn), uid, 'damaged')
    assert token(conn, uid) == first
    assert conn.execute('SELECT COUNT(DISTINCT token)=COUNT(*) FROM unit_codes').fetchone()[0]
    for table in ('unit_codes', 'sheet_copies', 'sheet_voids'):
        for sql in (f'UPDATE {table} SET unit_id=unit_id', f'DELETE FROM {table}'):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(sql)


def test_copies_require_reason_and_old_copy_still_marks(conn):
    uid = fresh(conn)
    assert print_one(conn, uid)['copies'][0]['copy'] == 1
    with pytest.raises(domain.DomainError, match='already has a sheet.*pick a reason'):
        print_one(conn, uid)
    other = fresh(conn)
    result = p.print_sheets(conn, manager(conn), scope='units', unit_ids=[uid, other])
    assert result['printed'] == 1 and result['skipped'][0]['unit_id'] == uid
    assert print_one(conn, uid, reason='lost')['copies'][0]['copy'] == 2
    scan = p.resolve(conn, token(conn, uid) + '/1')
    assert scan['ok_to_mark'] and 'copy 2' in ' '.join(scan['warnings'])
    with pytest.raises(domain.DomainError, match='never printed'):
        p.resolve(conn, token(conn, uid) + '/3')


def test_idempotent_batch(conn):
    uid = fresh(conn)
    a = print_one(conn, uid, idem_key='print-once')
    before = counts(conn)
    b = print_one(conn, uid, idem_key='print-once')
    assert b['batch_id'] == a['batch_id'] and b['already']
    assert counts(conn) == before


def test_changed_order_is_visible_until_reprint(conn):
    uid = fresh(conn)
    print_one(conn, uid)
    oid = domain._unit_order(conn, uid)['id']
    conn.execute('UPDATE orders SET variant=? WHERE id=?', ('new fictional fabric', oid))
    conn.commit()
    state = p.sheet_state(conn, uid)
    assert state['stale'] and [(c['field'], c['was'], c['now']) for c in state['changes']] == [('fabric', 'fictional fabric', 'new fictional fabric')]
    assert uid in p.units_for_scope(conn, 'changed')
    assert uid in [r['unit_id'] for r in p.attention(conn)['changed']]
    p.print_sheets(conn, manager(conn), scope='changed')
    assert not p.sheet_state(conn, uid)['stale']
    assert 'fictional fabric' in ' '.join(p.resolve(conn, token(conn, uid) + '/1')['warnings'])


def test_cancel_and_void_are_visible_and_repeat_safe(conn):
    uid = fresh(conn)
    print_one(conn, uid)
    oid = domain._unit_order(conn, uid)['id']
    conn.execute('UPDATE orders SET cancelled=1 WHERE id=?', (oid,))
    conn.commit()
    with pytest.raises(domain.DomainError, match='cancelled'):
        print_one(conn, uid, reason='changed')
    scan = p.resolve(conn, token(conn, uid))
    assert not scan['ok_to_mark'] and 'Put the item aside' in ' '.join(scan['warnings'])
    label = domain.unit_label(conn, uid)
    assert label in p.attention(conn)['cancelled_with_live_sheet']
    p.void_sheet(conn, manager(conn), uid, 'cancelled')
    before = counts(conn)
    assert 'Already' in p.void_sheet(conn, manager(conn), uid, 'cancelled')['message']
    assert counts(conn) == before
    assert label not in p.attention(conn)['cancelled_with_live_sheet']


@pytest.mark.parametrize('value,copy', [('http://x/u/K7QM2HXP/2', 2), ('/u/K7QM2HXP', None), ('K7QM-2HXP', None), ('k7qm 2hxp', None), ('K7QM2HXP/2', 2), ('K7QM2HXP.2', 2)])
def test_parse_forms(value, copy):
    assert p.parse_code(value) == ('K7QM2HXP', copy)


def test_unknown_and_garbage_codes(conn):
    with pytest.raises(domain.DomainError, match='not a Floorhand'):
        p.parse_code('garbage')
    with pytest.raises(domain.DomainError, match='not known'):
        p.resolve(conn, '22222222')


def test_route_sheet_escaped_complete_and_no_money(conn):
    uid = fresh(conn, '<script>alert("bad")</script>')
    result = print_one(conn, uid)
    data = p.batch_data(conn, result['batch_id'])
    html = h.render_batch(data, base='http://local.example')
    assert '#' + str(domain._unit_order(conn, uid)['number']) in html
    assert html.count('data-stage=') == len(domain.stages(conn))
    assert '<svg' in html and p.format_code(token(conn, uid)) in html
    assert '<script>alert' not in html and '&lt;script&gt;' in html
    assert domain.currency(conn) not in html
    price = conn.execute('SELECT price FROM products WHERE id=1').fetchone()[0]
    assert str(price) not in html
    assert 'URGENT' in html
    assert 'addEventListener' not in html


def test_invoice_uses_domain_total_and_previous_balance(conn, monkeypatch):
    sh = conn.execute('SELECT * FROM shipments ORDER BY created_at DESC LIMIT 1').fetchone()
    expected = sum(domain.shipment_total(conn, r['id']) - r['paid_amount'] for r in conn.execute('SELECT * FROM shipments WHERE customer_id=? AND created_at<? AND id<>?', (sh['customer_id'], sh['created_at'], sh['id'])))
    data = p.note_data(conn, sh['id'])
    assert data['total'] == domain.shipment_total(conn, sh['id'])
    assert data['previous_owed'] == expected
    assert data['owed_with_previous'] == expected + data['total'] - sh['paid_amount']
    html = h.render_note(data)
    assert 'Shop copy' in html and 'Customer copy' in html
    assert '<script' not in h.render_note(data, copies=1, toolbar=False)
    original = domain.shipment_total
    monkeypatch.setattr(domain, 'shipment_total', lambda c, sid: original(c, sid) + 17)
    assert p.note_data(conn, sh['id'])['total'] == data['total'] + 17


def test_no_duplicate_total_formula():
    for module in (p, h):
        assert 'SUM(' not in inspect.getsource(module).upper()


def test_email_missing_dry_run_repeat_and_explicit_resend(conn):
    sid = conn.execute('SELECT id FROM shipments ORDER BY id LIMIT 1').fetchone()[0]
    data = p.note_data(conn, sid)
    conn.execute('DELETE FROM customer_contacts WHERE customer_id=?', (data['customer_id'],))
    conn.commit()
    with pytest.raises(domain.DomainError, match=data['customer'].replace('&', r'\&')):
        p.email_note(conn, manager(conn), sid)
    addr = 'orders@fictional-recipient.example.com'
    a = p.email_note(conn, manager(conn), sid, to=addr, idem_key='email-once')
    assert a['message'] == 'Saved to outbox (sending is off).'
    row = conn.execute('SELECT * FROM outbox WHERE id=?', (a['outbox_id'],)).fetchone()
    assert row['status'] == 'draft' and 'Total: $' in row['body'] and 'FACT' not in row['body']
    assert '<svg' not in row['attachment_html']
    before = counts(conn)
    assert p.email_note(conn, manager(conn), sid, to=addr)['already']
    assert p.email_note(conn, manager(conn), sid, to=addr, resend=True, idem_key='email-once')['already']
    assert counts(conn) == before
    assert p.email_note(conn, manager(conn), sid, to=addr, resend=True)['outbox_id'] != a['outbox_id']
    assert 'Sending is off' in p.send_outbox(conn, manager(conn), a['outbox_id'])['message']
    assert 'attachment_html' not in p.outbox_list(conn)[0]
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute('UPDATE outbox SET subject=? WHERE id=?', ('changed', a['outbox_id']))


class FakeSMTP:
    fail = False
    messages = []

    def __init__(self, host, port, timeout):
        assert host == 'smtp.example' and port == 587 and timeout == 20

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        if self.fail:
            raise OSError('Fictional mail server unavailable')
        self.messages.append(message)


def test_smtp_send_failure_and_retry(conn, monkeypatch):
    monkeypatch.setenv('FLOORHAND_SMTP_HOST', 'smtp.example')
    monkeypatch.setenv('FLOORHAND_SMTP_FROM', 'office@fictional-shop.example.com')
    monkeypatch.setattr(p.smtplib, 'SMTP', FakeSMTP)
    sid = conn.execute('SELECT id FROM shipments LIMIT 1').fetchone()[0]
    FakeSMTP.fail = False
    a = p.email_note(conn, manager(conn), sid, to='new@fictional.example.com', resend=True)
    assert a['message'].startswith('Sent to')
    assert conn.execute('SELECT status FROM outbox WHERE id=?', (a['outbox_id'],)).fetchone()[0] == 'sent'
    assert 'Already sent' in p.send_outbox(conn, manager(conn), a['outbox_id'])['message']
    FakeSMTP.fail = True
    b = p.email_note(conn, manager(conn), sid, to='other@fictional.example.com', resend=True)
    assert not b['ok'] and 'Not sent:' in b['message'] and 'try again later' in b['message']
    FakeSMTP.fail = False
    assert p.send_outbox(conn, manager(conn), b['outbox_id'])['ok']
    assert list(FakeSMTP.messages[-1].iter_attachments())[0].get_content_type() == 'text/html'


def test_email_validation_and_office_role(conn):
    for bad in ('bad', 'x @y.example', 'x@y', 'a'*255 + '@x.example'):
        with pytest.raises(domain.DomainError, match='e-mail'):
            p.set_customer_email(conn, manager(conn), 1, bad)
    worker = conn.execute("SELECT id FROM people WHERE role='worker' LIMIT 1").fetchone()[0]
    with pytest.raises(domain.DomainError, match='office'):
        p.set_customer_email(conn, worker, 1, 'orders@fictional.example.com')
    p.set_customer_email(conn, manager(conn), 1, 'orders@fictional.example.com')
    assert p.customer_email(conn, 1) == 'orders@fictional.example.com'


def test_seed_attention_and_reproducible_history(conn, tmp_path):
    assert conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0] - conn.execute('SELECT COUNT(*) FROM customer_contacts').fetchone()[0] == 2
    assert all(r[0].endswith('.example.com') for r in conn.execute('SELECT email FROM customer_contacts'))
    for key in ('shop_address', 'shop_phone', 'shop_email', 'shop_tax_id'):
        assert conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()[0]
    attention = p.attention(conn)
    assert len(attention['changed']) >= 2 and len(attention['cancelled_with_live_sheet']) >= 1
    c = db.connect(tmp_path / 'repeat.db')
    seed.build(c, random.Random(1), today=date.today())
    for table in ('unit_codes', 'print_batches', 'sheet_copies', 'outbox'):
        assert [tuple(r) for r in conn.execute(f'SELECT * FROM {table}')] == [tuple(r) for r in c.execute(f'SELECT * FROM {table}')]
    c.close()


def test_api_permissions_readonly_pages_and_scan(conn, monkeypatch):
    path = conn.execute('PRAGMA database_list').fetchone()['file']
    monkeypatch.setenv('FLOORHAND_DB', path)
    from floorhand.main import app
    client = TestClient(app)
    assert client.get('/api/paperwork/outbox').status_code == 401
    assert client.get('/paperwork').status_code == 401
    uid = fresh(conn)
    result = print_one(conn, uid)
    code = token(conn, uid)
    worker = conn.execute("SELECT id FROM people WHERE role='worker' LIMIT 1").fetchone()[0]
    client.post('/api/login', json=dict(person_id=worker, tablet=True))
    assert client.get('/api/paperwork/outbox').status_code == 403
    assert client.get('/paperwork').status_code == 403
    response = client.get('/api/paperwork/resolve', params={'code':code})
    assert response.status_code == 200
    def check(value):
        if isinstance(value, dict):
            assert not any(any(word in k for word in ('price', 'amount', 'total')) for k in value)
            for v in value.values():
                check(v)
        elif isinstance(value, list):
            for v in value:
                check(v)
    check(response.json())
    client.post('/api/login', json=dict(person_id=manager(conn)))
    result = client.post('/api/paperwork/sheets/print', json=dict(scope='units', unit_ids=[uid], reason='lost', idem_key='api-print')).json()
    assert result['ok']
    sid = conn.execute('SELECT id FROM shipments LIMIT 1').fetchone()[0]
    oid = domain._unit_order(conn, uid)['id']
    before = counts(conn)
    for layout in ('a5', 'a4', 'a4x4'):
        page = client.get(result['url'], params=dict(layout=layout))
        assert page.status_code == 200 and '<svg' in page.text
    for url in (f'/paperwork/sheets/order/{oid}', f'/paperwork/note/{sid}', '/paperwork', '/api/paperwork/outbox', '/paperwork/outbox/1'):
        assert client.get(url).status_code == 200
    response = client.get(f'/u/{code}/1', follow_redirects=False)
    assert response.status_code == 302 and response.headers['location'] == f'/?code={code}/1'
    assert counts(conn) == before
    assert client.get('/paperwork/sheets/batch/999999').status_code == 409


def test_batch_retry_preserves_original_skipped_explanation(conn):
    old, new = fresh(conn), fresh(conn)
    print_one(conn, old)
    first = p.print_sheets(conn, manager(conn), scope='units', unit_ids=[old, new], idem_key='mixed')
    print_one(conn, old, reason='lost')
    again = p.print_sheets(conn, manager(conn), scope='units', unit_ids=[old, new], idem_key='mixed')
    assert again == dict(first, already=True)


def test_token_collision_retries_without_replacing_existing(conn, monkeypatch):
    old = conn.execute('SELECT token FROM unit_codes LIMIT 1').fetchone()[0]
    uid = fresh(conn)
    generated = iter(old + 'ZZZZZZZZ')
    monkeypatch.setattr(p.secrets, 'choice', lambda alphabet: next(generated))
    assert token(conn, uid) == 'ZZZZZZZZ'
    assert conn.execute('SELECT COUNT(*) FROM unit_codes WHERE token=?', (old,)).fetchone()[0] == 1


def test_original_print_text_survives_order_change(conn):
    uid = fresh(conn, 'original fictional fabric')
    result = print_one(conn, uid)
    original = h.render_batch(p.batch_data(conn, result['batch_id']))
    conn.execute('UPDATE orders SET variant=? WHERE id=?', ('replacement fictional fabric', domain._unit_order(conn, uid)['id']))
    conn.commit()
    assert h.render_batch(p.batch_data(conn, result['batch_id'])) == original
    assert 'original fictional fabric' in original


def test_office_tablet_cannot_open_money_pages(conn, monkeypatch):
    monkeypatch.setenv('FLOORHAND_DB', conn.execute('PRAGMA database_list').fetchone()['file'])
    from floorhand.main import app
    client = TestClient(app)
    client.post('/api/login', json=dict(person_id=manager(conn), tablet=True))
    sid = conn.execute('SELECT id FROM shipments LIMIT 1').fetchone()[0]
    for url in ('/paperwork', '/api/paperwork/notes', '/api/paperwork/outbox', f'/paperwork/note/{sid}', '/paperwork/outbox/1'):
        assert client.get(url).status_code == 403


def test_unprinted_order_summary_is_readonly(conn, monkeypatch):
    uid = fresh(conn)
    monkeypatch.setenv('FLOORHAND_DB', conn.execute('PRAGMA database_list').fetchone()['file'])
    from floorhand.main import app
    client = TestClient(app)
    client.post('/api/login', json=dict(person_id=manager(conn)))
    before = list(conn.iterdump())
    response = client.get('/paperwork/sheets/order/' + str(domain._unit_order(conn, uid)['id']))
    assert response.status_code == 200
    assert 'no code yet' in response.text
    assert list(conn.iterdump()) == before


def test_scopes_explain_skips_and_reject_unknown(conn):
    with pytest.raises(domain.DomainError, match='No such item'):
        p.print_sheets(conn, manager(conn), scope='units', unit_ids=[999999])
    with pytest.raises(domain.DomainError, match='No such order'):
        p.print_sheets(conn, manager(conn), scope='orders', order_ids=[999999])
    with pytest.raises(domain.DomainError, match='Choose what to print'):
        p.print_sheets(conn, manager(conn), scope='everything')
    shipped = conn.execute('SELECT u.order_id FROM shipment_lines sl JOIN units u ON u.id = sl.unit_id '
                           'LIMIT 1').fetchone()[0]
    with pytest.raises(domain.DomainError, match='has shipped'):
        p.print_sheets(conn, manager(conn), scope='orders', order_ids=[shipped])
    worker = conn.execute("SELECT id FROM people WHERE role='worker' LIMIT 1").fetchone()[0]
    with pytest.raises(domain.DomainError, match='office'):
        p.print_sheets(conn, worker, scope='new')


def test_new_scope_prints_each_item_once(conn):
    uid = fresh(conn)
    assert uid in p.units_for_scope(conn, 'new')
    first = p.print_sheets(conn, manager(conn), scope='new')
    assert uid in [c['unit_id'] for c in first['copies']]
    assert p.attention(conn)['counts']['new'] == 0
    with pytest.raises(domain.DomainError, match='already has a sheet'):
        p.print_sheets(conn, manager(conn), scope='new')


def test_seed_shows_both_kinds_of_change(conn):
    fields = {c['field'] for item in p.attention(conn)['changed'] for c in item['changes']}
    assert {'fabric', 'due date'} <= fields
    reprints = conn.execute("SELECT COUNT(*) FROM sheet_copies WHERE copy = 2").fetchone()[0]
    assert reprints == 3


def test_urgent_only_when_due_soon(conn):
    uid = fresh(conn)
    oid = domain._unit_order(conn, uid)['id']
    conn.execute("UPDATE orders SET due_date = '2999-01-01' WHERE id = ?", (oid,))
    conn.commit()
    html = h.render_batch(p.batch_data(conn, print_one(conn, uid)['batch_id']))
    assert 'URGENT' not in html and 'class="sheet"' in html.replace('class="sheet urgent"', '')


def test_long_note_puts_each_copy_on_its_own_page(conn):
    sid = conn.execute('SELECT id FROM shipments LIMIT 1').fetchone()[0]
    data = p.note_data(conn, sid)
    short = dict(data, lines=data['lines'][:1])
    long = dict(data, lines=data['lines'][:1] * 9)
    assert 'note alone' not in h.render_note(short)
    assert h.render_note(long).count('note alone') == 2


def test_russian_paper_and_mail(conn):
    sid = conn.execute('SELECT id FROM shipments LIMIT 1').fetchone()[0]
    assert 'Накладная' in h.render_note(p.note_data(conn, sid), lang='ru')
    conn.execute("UPDATE people SET lang = 'ru' WHERE id = ?", (manager(conn),))
    conn.commit()
    r = p.email_note(conn, manager(conn), sid, to='ru@fictional.example.com')
    body = conn.execute('SELECT subject, body FROM outbox WHERE id = ?', (r['outbox_id'],)).fetchone()
    assert body['subject'].startswith('Накладная') and 'Итого' in body['body']


def test_mail_server_is_called_without_holding_the_database(conn, tmp_path, monkeypatch):
    """A slow mail server must not stop marks on the floor."""
    path = conn.execute('PRAGMA database_list').fetchone()['file']
    monkeypatch.setenv('FLOORHAND_SMTP_HOST', 'smtp.example')
    monkeypatch.setenv('FLOORHAND_SMTP_FROM', 'office@fictional-shop.example.com')

    class WritingSMTP(FakeSMTP):
        def send_message(self, message):
            other = sqlite3.connect(path, timeout=0.2)
            other.execute("INSERT INTO settings (key, value) VALUES ('probe', '1')")
            other.commit()
            other.close()

    monkeypatch.setattr(p.smtplib, 'SMTP', WritingSMTP)
    sid = conn.execute('SELECT id FROM shipments LIMIT 1').fetchone()[0]
    r = p.email_note(conn, manager(conn), sid, to='lock@fictional.example.com')
    assert r['status'] == 'sent', r['message']


def test_attention_is_set_based(conn):
    """The floor has thousands of items: attention must not query per item."""
    calls = []
    conn.set_trace_callback(calls.append)
    p.attention(conn)
    conn.set_trace_callback(None)
    assert len(calls) < 20


def test_skips_of_one_kind_are_one_sentence(conn):
    oid = domain.create_order(conn, product_id=1, qty=3, customer_id=1, due_date=None,
                              variant='fictional fabric', source='manual')
    p.print_sheets(conn, manager(conn), scope='orders', order_ids=[oid])
    with pytest.raises(domain.DomainError) as err:
        p.print_sheets(conn, manager(conn), scope='orders', order_ids=[oid])
    text = str(err.value)
    assert text.count('pick a reason') == 1 and 'already have a sheet' in text
