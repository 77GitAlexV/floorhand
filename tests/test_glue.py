"""Glue-covered hands still require a human decision and a complete specification."""
import json
import random
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from floorhand import db, domain, intake, models, seed
from floorhand.domain import DomainError
from floorhand.features import gate, marking as mk, planner, slang


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.delenv('FLOORHAND_META_LIVE', raising=False)
    path = tmp_path / 'glue.db'
    monkeypatch.setenv('FLOORHAND_DB', str(path))
    c = db.connect(path)
    seed.build(c, random.Random(7))
    yield c
    c.close()


def worker(conn, code):
    return conn.execute('SELECT p.id FROM people p JOIN stages s ON s.id=p.stage_id '
                        'WHERE s.code=? LIMIT 1', (code,)).fetchone()[0]


def stage(conn, code):
    return conn.execute('SELECT id FROM stages WHERE code=?', (code,)).fetchone()[0]


def office(conn):
    return conn.execute("SELECT id FROM people WHERE role='manager'").fetchone()[0]


def new_order(conn, *, product='Harbor armchair', qty=2, variant='velvet beige · walnut · curved legs'):
    pid = conn.execute('SELECT id FROM products WHERE name=?', (product,)).fetchone()[0]
    oid = domain.create_order(conn, product_id=pid, qty=qty, customer_id=None,
                              due_date=None, variant=variant, source='glue-test')
    return oid, [r[0] for r in conn.execute('SELECT id FROM units WHERE order_id=? ORDER BY seq', (oid,))]


def assembly_batch(conn):
    oid, units = new_order(conn)
    actor = mk.Actor(conn.execute('SELECT id FROM people WHERE stage_id=? ORDER BY id LIMIT 1 OFFSET 1',
                                  (stage(conn, 'asm'),)).fetchone()[0])
    for uid in units:
        for code in ('cut', 'sew', 'uph'):
            domain.mark_done(conn, worker(conn, code), uid)
        conn.execute('INSERT INTO assignments VALUES (?,?,?)', (uid, stage(conn, 'asm'), actor.person_id))
    conn.commit()
    return actor, oid, units


def count(conn, table='work_events'):
    return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def test_gate_blocks_queue_take_direct_mark_and_plan(conn):
    oid, units = new_order(conn, variant='velvet beige')
    before = count(conn)
    assert gate.check(conn, oid)['missing'] == ['leg_finish']
    assert 'leg finish not given for Harbor armchair' in gate.check(conn, oid)['message']
    assert units[0] not in domain._waiting_units(conn)[stage(conn, 'cut')]
    assert units[0] not in {u.id for u in planner.open_units(conn, domain.now(conn).date())}
    with pytest.raises(DomainError, match='Cannot start'):
        mk.mark(conn, mk.Actor(worker(conn, 'cut')), unit_id=units[0])
    with pytest.raises(DomainError):
        domain.take(conn, worker(conn, 'cut'), oid)
    assert count(conn) == before
    assert gate.summary(conn)['held_lines'] >= 1


def test_family_defaults_and_new_catalog_products(conn):
    pouf, _ = new_order(conn, product='Round pouf', variant='velvet beige')
    chair, _ = new_order(conn, variant='velvet beige · oak')
    assert gate.check(conn, pouf)['ready'] and gate.check(conn, chair)['ready']
    pid = conn.execute("INSERT INTO products(name,kind,price) VALUES ('Demo Mist table','table',47)").lastrowid
    assert [r[0] for r in conn.execute('SELECT field FROM product_requirements WHERE product_id=?', (pid,))] == ['frame_colour']


def test_gate_question_is_bilingual_quoted_dry_run_and_repeat_safe(conn):
    oid, _ = new_order(conn, variant='velvet beige')
    conn.execute('INSERT INTO gate_sources VALUES (?,?)', (oid, 'Harbor, two, beige velvet / Харбор, два, велюр беж'))
    conn.commit()
    a = gate.question(conn, office(conn), oid)
    b = gate.question(conn, office(conn), oid)
    assert a['outbox_id'] == b['outbox_id'] and a['dry_run']
    row = conn.execute('SELECT * FROM outbox WHERE id=?', (a['outbox_id'],)).fetchone()
    assert row['status'] == 'draft' and row['to_addr'] is None
    assert 'Please specify leg finish' in row['body'] and 'Уточните' in row['body']
    assert gate.check(conn, oid)['source_quote'] in row['body']


@pytest.mark.parametrize('text', ['Leg finish: walnut', 'Ножки ореховые'])
def test_answer_proposal_writes_only_action_then_confirmation_releases(conn, text):
    oid, units = new_order(conn, variant='velvet beige')
    before = count(conn)
    p = gate.propose_answer(conn, office(conn), oid, text)
    assert p['values']['leg_finish'] == {'value': 'walnut', 'quote': text}
    assert not gate.check(conn, oid)['ready'] and count(conn) == before
    applied = gate.apply_answer(conn, office(conn), p['id'])
    assert applied['ready'] and applied['status'] == 'applied'
    assert units[0] in domain._waiting_units(conn)[stage(conn, 'cut')]
    assert gate.apply_answer(conn, office(conn), p['id'])['status'] == 'already'
    assert mk.mark(conn, mk.Actor(worker(conn, 'cut')), unit_id=units[0])['status'] == 'recorded'


def test_gate_intake_keeps_original_quote(conn):
    p = intake.propose(conn, office(conn), 'One Harbor armchair to stock, fabric: velvet beige')
    intake.apply(conn, office(conn), p['id'], {})
    payload = json.loads(conn.execute('SELECT payload FROM agent_actions WHERE id=?', (p['id'],)).fetchone()[0])
    oid = payload['items'][0]['order_id']
    assert oid and gate.check(conn, oid)['source_quote'] == payload['items'][0]['line']
    assert not gate.check(conn, oid)['ready']


@pytest.mark.parametrize('text', ['oak or walnut', 'не walnut', 'maybe oak?', 'anything you like'])
def test_ambiguous_answers_do_not_supply_options(conn, text):
    oid, _ = new_order(conn, variant='velvet beige')
    p = gate.propose_answer(conn, office(conn), oid, text)
    assert not p['can_apply']
    with pytest.raises(DomainError, match='No unambiguous'):
        gate.apply_answer(conn, office(conn), p['id'])
    assert not gate.check(conn, oid)['ready']


def test_gate_stale_answer_and_other_person_cannot_apply(conn):
    oid, _ = new_order(conn, variant='velvet beige')
    p = gate.propose_answer(conn, office(conn), oid, 'oak')
    with pytest.raises(DomainError, match='yours'):
        gate.apply_answer(conn, worker(conn, 'cut'), p['id'])
    conn.execute('UPDATE orders SET variant=? WHERE id=?', ('velvet graphite', oid))
    conn.commit()
    with pytest.raises(DomainError, match='Order changed'):
        gate.apply_answer(conn, office(conn), p['id'])


def test_cancelled_or_started_orders_cannot_use_gate_to_skip_change_review(conn):
    oid, units = new_order(conn, variant='velvet beige')
    p = gate.propose_answer(conn, office(conn), oid, 'walnut')
    conn.execute('UPDATE orders SET cancelled=1 WHERE id=?', (oid,))
    conn.commit()
    with pytest.raises(DomainError, match='cancelled'):
        gate.apply_answer(conn, office(conn), p['id'])
    conn.execute('UPDATE orders SET cancelled=0,variant=? WHERE id=?', ('velvet beige · walnut', oid))
    conn.commit()
    domain.mark_done(conn, worker(conn, 'cut'), units[0])
    conn.execute('UPDATE orders SET variant=? WHERE id=?', ('velvet beige', oid))
    conn.commit()
    p = gate.propose_answer(conn, office(conn), oid, 'walnut')
    with pytest.raises(DomainError, match='Work has started'):
        gate.apply_answer(conn, office(conn), p['id'])


def test_gate_undo_does_not_bypass_missing_options(conn):
    oid, (uid,) = new_order(conn, qty=1)
    person = worker(conn, 'cut')
    event = domain.mark_done(conn, person, uid)
    domain.undo(conn, person, event.event_id)
    conn.execute('UPDATE orders SET variant=? WHERE id=?', ('', oid))
    conn.commit()
    assert oid in gate.held_orders(conn)
    assert uid not in domain._waiting_units(conn)[stage(conn, 'cut')]


def test_slang_specific_item_option_and_trade_proposes_only(conn):
    actor, oid, units = assembly_batch(conn)
    before = count(conn)
    p = mk.propose(conn, actor, 'собрал два харбора на кривых ногах')
    assert p['model'] == 'offline-dictionary' and p['can_apply'] == 2
    assert [i['unit_id'] for i in p['items']] == units
    assert count(conn) == before
    assert mk.decide(conn, actor, p['id'], True)['ok']
    assert count(conn) == before + 2
    assert mk.decide(conn, actor, p['id'], True)['status'] == 'already'
    assert count(conn) == before + 2


def test_stale_voice_option_and_expired_proposal_need_new_confirmation(conn, monkeypatch):
    actor, oid, units = assembly_batch(conn)
    p = slang.propose(conn, actor, 'assembled two harbor on curved legs')
    conn.execute('UPDATE orders SET variant=? WHERE id=?', ('velvet beige · walnut · straight legs', oid))
    conn.commit()
    before = count(conn)
    with pytest.raises(DomainError, match='Order changed'):
        mk.decide(conn, actor, p['id'], True)
    assert count(conn) == before
    monkeypatch.setattr(mk, '_now', lambda c: domain.now(c) + timedelta(hours=1))
    with pytest.raises(DomainError, match='too long ago'):
        mk.decide(conn, actor, p['id'], True)


def test_seeded_headline_phrase_has_one_assigned_demo_batch(conn):
    actor = mk.Actor(worker(conn, 'asm'))
    p = slang.propose(conn, actor, 'собрал два харбора на кривых ногах')
    assert p['can_apply'] == 2
    assert all(domain._unit_order(conn, i['unit_id'])['source'] == 'slang-demo' for i in p['items'])
    assert conn.execute('SELECT COUNT(*) FROM slang_aliases WHERE learned_by IS NULL').fetchone()[0] >= 40


def test_learning_happens_after_fix_and_confirm_only(conn, monkeypatch):
    actor, oid, units = assembly_batch(conn)
    phrase = 'два харбора дожал на дугах'
    before = count(conn)
    p = slang.propose(conn, actor, phrase)
    meaning = {'product_id': domain._unit_order(conn, units[0])['product_id'], 'qty': 2,
               'stage_id': stage(conn, 'asm'), 'option': 'curved legs', 'component': None}
    fixed = slang.fix(conn, actor, p['id'], meaning)
    assert fixed['can_apply'] == 2 and count(conn) == before
    assert slang.offline(conn, phrase, stage(conn, 'asm')) is None
    assert mk.decide(conn, actor, p['id'], True)['ok']
    row = conn.execute('SELECT * FROM slang_aliases WHERE phrase=?', (slang.norm(phrase),)).fetchone()
    assert row['learned_by'] == actor.person_id and row['learned_at'] and row['action_id'] == p['id']
    assert count(conn, 'slang_alias_revisions') == 1
    assert slang.offline(conn, phrase, stage(conn, 'asm')) == meaning
    monkeypatch.setattr(models, 'respond', lambda *a, **kw: pytest.fail('learned aliases must resolve offline'))
    again = slang.propose(conn, actor, phrase)
    assert again['meaning'] == meaning


def test_rejected_fix_never_learns_and_no_button_never_undoes(conn):
    actor, oid, units = assembly_batch(conn)
    p = slang.propose(conn, actor, 'new invented nickname')
    meaning = {'product_id': domain._unit_order(conn, units[0])['product_id'], 'qty': 2,
               'stage_id': stage(conn, 'asm'), 'option': 'curved legs'}
    slang.fix(conn, actor, p['id'], meaning)
    before = count(conn)
    assert mk.decide(conn, actor, p['id'], False)['status'] == 'rejected'
    assert mk.decide(conn, actor, p['id'], True)['status'] == 'already'
    assert slang.offline(conn, 'new invented nickname', stage(conn, 'asm')) is None
    assert count(conn) == before


def test_components_are_visible_append_only_counts_not_whole_chairs(conn):
    actor = mk.Actor(worker(conn, 'sew'), show_money=False)
    before = count(conn)
    p = slang.propose(conn, actor, 'три сидушки на харбор')
    assert p['meaning']['component'] == 'seat cushions' and p['can_apply'] == 1
    assert count(conn, 'slang_component_events') == 0
    assert mk.decide(conn, actor, p['id'], True)['ok']
    mk.decide(conn, actor, p['id'], True)
    assert count(conn) == before and count(conn, 'slang_component_events') == 1
    row = conn.execute('SELECT * FROM slang_component_events').fetchone()
    assert row['qty'] == 3 and row['work_date'] == domain.today(conn)
    for sql in ('UPDATE slang_component_events SET qty=4', 'DELETE FROM slang_component_events'):
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            conn.execute(sql)


def test_no_cross_trade_partial_batch_or_ambiguous_guess(conn):
    actor, oid, units = assembly_batch(conn)
    p = slang.propose(conn, mk.Actor(worker(conn, 'pack')), 'assembled two harbor with curved legs')
    assert not p['can_apply'] and 'another trade' in p['message']
    with pytest.raises(DomainError, match='another trade'):
        mk.decide(conn, mk.Actor(worker(conn, 'pack')), p['id'], True)
    p = slang.propose(conn, actor, 'assembled five harbor on curved legs')
    assert not p['can_apply']
    # With the identifying assignments removed, multiple eligible orders must be scanned.
    conn.execute('DELETE FROM assignments WHERE stage_id=?', (stage(conn, 'asm'),))
    conn.commit()
    assembly_batch(conn)
    conn.execute('DELETE FROM assignments WHERE stage_id=?', (stage(conn, 'asm'),))
    conn.commit()
    assert not slang.propose(conn, actor, 'assembled two harbor on curved legs')['can_apply']


@pytest.mark.parametrize('phrase', ['not assembled two harbor', 'не собрал два харбора', 'maybe two harbor?', 'завтра соберу два харбора'])
def test_negative_and_future_words_never_call_model(conn, monkeypatch, phrase):
    monkeypatch.setattr(models, 'respond', lambda *a, **kw: pytest.fail('unsafe phrase reached model'))
    p = slang.propose(conn, mk.Actor(worker(conn, 'asm')), phrase)
    assert p['status'] == 'nothing' and not p['can_apply']


def test_spark_gets_catalog_dictionary_and_only_proposes(conn, monkeypatch):
    actor, oid, units = assembly_batch(conn)
    meaning = {'product_id': domain._unit_order(conn, units[0])['product_id'], 'qty': 2,
               'stage_id': stage(conn, 'asm'), 'option': 'curved legs'}
    def fake(c, purpose, prompt, **kwargs):
        assert purpose == 'slang-resolve' and 'Shop dictionary' in prompt and 'Harbor armchair' in prompt
        assert kwargs['max_output_tokens'] == 350
        return {'output_text': json.dumps({'meaning': meaning, 'quote': 'done the bendy pair'})}
    monkeypatch.setattr(models, 'respond', fake)
    before = count(conn)
    p = slang.propose(conn, actor, 'done the bendy pair')
    assert p['can_apply'] == 2 and p['model'] == models.SPARK and count(conn) == before


@pytest.mark.parametrize('response', ['garbage', '{"meaning":null}', '{"meaning":{"qty":999},"quote":"made up quote"}'])
def test_broken_or_ungrounded_model_answer_stays_unconfirmable(conn, monkeypatch, response):
    monkeypatch.setattr(models, 'respond', lambda *a, **kw: {'output_text': response})
    p = slang.propose(conn, mk.Actor(worker(conn, 'asm')), 'an unfamiliar phrase')
    assert not p['can_apply']


def test_slang_eval_corpus(conn):
    rows = [json.loads(line) for line in (Path(__file__).parent / 'eval/slang.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows) >= 40 and sum(r['style'] == 'different' for r in rows) >= len(rows) // 2
    names = {r['name']: r['id'] for r in conn.execute('SELECT id,name FROM products')}
    correct, silent, accepted, abstained = 0, 0, 0, 0
    for row in rows:
        actual = slang.offline(conn, row['text'], stage(conn, row.get('stage', 'asm')))
        expected = None if row.get('reject') else {
            'product_id': names[row['product']], 'qty': row['qty'], 'stage_id': stage(conn, row['stage']),
            'option': row.get('option'), 'component': row.get('component')}
        correct += actual == expected
        accepted += actual is not None
        abstained += actual is None
        silent += actual is not None and actual != expected
    print(f'FACT slang eval: {correct}/{len(rows)} correct; {silent}/{len(rows)} silent errors; '
          f'{accepted} resolved; {abstained} abstained')
    assert silent == 0
    assert correct / len(rows) >= .75


def test_http_roles_spoken_yes_review_delete_and_payload_rejection(conn):
    from floorhand.main import app
    actor, oid, units = assembly_batch(conn)
    client = TestClient(app)
    for path in ('/api/slang/catalog', '/api/slang/aliases', '/api/gate', '/api/gate/summary'):
        assert client.get(path).status_code == 401
    client.post('/api/login', json={'person_id': actor.person_id})
    assert client.get('/api/gate').status_code == 403
    assert client.get('/api/slang/aliases').status_code == 403
    assert 'price' not in client.get('/api/slang/catalog').text
    p = client.post('/api/slang/propose', json={'text': 'two bendy things done'}).json()
    fix = {'product_id': domain._unit_order(conn, units[0])['product_id'], 'qty': 2,
           'stage_id': stage(conn, 'asm'), 'option': 'curved legs'}
    assert client.post(f'/api/slang/{p["id"]}/fix', json=fix).json()['can_apply'] == 2
    assert client.post(f'/api/slang/{p["id"]}/decide', json={'spoken': 'yesterday'}).status_code == 409
    assert client.post(f'/api/slang/{p["id"]}/decide', json={'yes': True, 'qty': 99}).status_code == 422
    assert client.post(f'/api/slang/{p["id"]}/decide', json={'spoken': 'да'}).json()['ok']
    assert client.post(f'/api/slang/{p["id"]}/decide', json={'spoken': 'yes'}).json()['status'] == 'already'
    client.post('/api/login', json={'person_id': office(conn)})
    rows = client.get('/api/slang/aliases').json()['aliases']
    alias = next(a for a in rows if a['phrase'] == 'two bendy things done')
    assert client.delete(f'/api/slang/aliases/{alias["id"]}').json()['ok']
    assert slang.offline(conn, 'two bendy things done', stage(conn, 'asm')) is None
    deleted = conn.execute('SELECT * FROM slang_aliases WHERE id=?', (alias['id'],)).fetchone()
    assert deleted['deleted_by'] == office(conn) and deleted['deleted_at']


def test_gate_http_confirm_applies_stored_values_only(conn):
    from floorhand.main import app
    oid, units = new_order(conn, variant='velvet beige')
    client = TestClient(app)
    assert client.post(f'/api/gate/{oid}/answer', json={'text': 'oak'}).status_code == 401
    client.post('/api/login', json={'person_id': worker(conn, 'cut')})
    assert client.post(f'/api/gate/{oid}/answer', json={'text': 'oak'}).status_code == 403
    client.post('/api/login', json={'person_id': office(conn)})
    p = client.post(f'/api/gate/{oid}/answer', json={'text': 'oak'}).json()
    r = client.post(f'/api/gate/answers/{p["id"]}/confirm', json={'values': {'leg_finish': 'walnut'}})
    assert r.json()['ready']
    assert 'leg finish: oak' in gate.check(conn, oid)['variant']
    assert 'walnut' not in gate.check(conn, oid)['variant']
    assert client.post(f'/api/gate/answers/{p["id"]}/confirm', json={}).json()['status'] == 'already'


def test_handsfree_surface_readback_and_gate_banner():
    static = Path(__file__).parents[1] / 'floorhand/static/js/screens'
    src = (static / 'handsfree.js').read_text(encoding='utf-8')
    assert 'min-height:240px' in src and 'min-height:140px' in src
    assert 'speechSynthesis' in src and 'utterance.onend' in src
    assert 'pointerdown' in src and 'pointerup' in src and '{ spoken }' in src
    assert '/api/gate/summary' in (static / 'floor.js').read_text(encoding='utf-8')
