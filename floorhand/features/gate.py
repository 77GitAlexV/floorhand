"""Keep missing specifications in the office, before a piece reaches cutting."""
import json
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import domain, intake
from ..domain import DomainError
from ..web import Session, get_conn, office, session

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_requirements (
    product_id INTEGER NOT NULL REFERENCES products(id),
    field TEXT NOT NULL CHECK(field IN ('fabric', 'leg_finish', 'frame_colour')),
    PRIMARY KEY(product_id, field)
);
CREATE TABLE IF NOT EXISTS gate_sources (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id),
    quote TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS gate_product_defaults AFTER INSERT ON products
BEGIN
    INSERT INTO product_requirements SELECT NEW.id, 'fabric'
        WHERE NEW.kind IN ('armchair','chair','rocking chair','pouf','sofa','daybed','bench');
    INSERT INTO product_requirements SELECT NEW.id, 'leg_finish'
        WHERE NEW.kind IN ('armchair','chair','rocking chair','sofa','daybed','bench');
    INSERT INTO product_requirements SELECT NEW.id, 'frame_colour'
        WHERE NEW.kind NOT IN ('armchair','chair','rocking chair','pouf','sofa','daybed','bench');
END;
-- Upgrade existing catalogs as well as products added after this feature loads.
INSERT OR IGNORE INTO product_requirements SELECT id, 'fabric' FROM products
    WHERE kind IN ('armchair','chair','rocking chair','pouf','sofa','daybed','bench');
INSERT OR IGNORE INTO product_requirements SELECT id, 'leg_finish' FROM products
    WHERE kind IN ('armchair','chair','rocking chair','sofa','daybed','bench');
INSERT OR IGNORE INTO product_requirements SELECT id, 'frame_colour' FROM products
    WHERE kind NOT IN ('armchair','chair','rocking chair','pouf','sofa','daybed','bench');
"""

LABELS = {'fabric': ('fabric', 'ткань'), 'leg_finish': ('leg finish', 'отделка ножек'),
          'frame_colour': ('frame colour', 'цвет каркаса')}


def stamp(conn):
    return domain.now(conn).isoformat(timespec='seconds')


def choices(conn, field):
    key = 'fabrics' if field == 'fabric' else 'frames'
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return json.loads(row[0]) if row else []


def extract(conn, text, field):
    """Only a unique catalog value is a specification; a blank is never a default."""
    labels = '|'.join(re.escape(s) for s in (field, *LABELS[field]))
    explicit = re.findall(r'(?:^|[·;\n])\s*(?:' + labels + r')\s*:\s*([^·;\n]+)', text, re.I)
    if explicit:
        text = ' '.join(explicit)
    low = text.casefold()
    if re.search(r'\b(?:not|no|unknown|нет|не|уточнить|потом)\b|\?', low):
        return None
    words = set(intake.norm(text).split())
    hits = [v for v in choices(conn, field) if set(intake.norm(v).split()) <= words]
    return hits[0] if len(hits) == 1 else None


def check(conn, order_id):
    row = conn.execute('SELECT o.*, p.name AS product FROM orders o JOIN products p '
                       'ON p.id=o.product_id WHERE o.id=?', (order_id,)).fetchone()
    if row is None:
        raise DomainError('No such order. / Такого заказа нет.')
    fields = [r[0] for r in conn.execute('SELECT field FROM product_requirements '
                                        'WHERE product_id=? ORDER BY field', (row['product_id'],))]
    missing = [f for f in fields if not extract(conn, row['variant'], f)]
    source = conn.execute('SELECT quote FROM gate_sources WHERE order_id=?', (order_id,)).fetchone()
    return {'order_id': order_id, 'number': row['number'], 'product': row['product'],
            'cancelled': bool(row['cancelled']),
            'variant': row['variant'], 'product_id': row['product_id'], 'missing': missing,
            'ready': not missing, 'source_quote': source[0] if source else row['variant'],
            'message': ' '.join(f"Cannot start: {LABELS[f][0]} not given for {row['product']}."
                                for f in missing) or 'Ready for cutting.',
            'message_ru': ' '.join(f"Нельзя начать: не указана {LABELS[f][1]} для «{row['product']}»."
                                   for f in missing) or 'Можно передать в раскрой.'}


def held_orders(conn):
    """Already-started work stays visible; the gate controls release to the first stage."""
    return {r[0] for r in conn.execute(
        'SELECT o.id FROM orders o WHERE o.cancelled=0 AND EXISTS '
        '(SELECT 1 FROM units u WHERE u.order_id=o.id AND NOT EXISTS '
        f'(SELECT 1 FROM ({domain.LIVE_DONE}) e WHERE e.unit_id=u.id AND e.stage_id='
        '(SELECT id FROM stages ORDER BY seq LIMIT 1)) AND NOT EXISTS '
        '(SELECT 1 FROM shipment_lines sl WHERE sl.unit_id=u.id))') if not check(conn, r[0])['ready']}


def assert_start(conn, order_id, lang='en'):
    status = check(conn, order_id)
    if not status['ready']:
        raise DomainError(status['message_ru' if lang == 'ru' else 'message'] +
                          (' Офис должен уточнить параметры.' if lang == 'ru' else
                           ' Ask the office to complete the specifications.'))


def summary(conn):
    n = len(held_orders(conn))
    return {'ok': True, 'held_lines': n, 'basis': 'FACT',
            'message': f'FACT: held at the gate: {n} lines.',
            'message_ru': f'ФАКТ: на входе задержано строк: {n}.'}


def question(conn, person_id, order_id):
    status = check(conn, order_id)
    if status['ready']:
        return {'ok': True, 'message': 'Ready for cutting. / Можно передать в раскрой.'}
    fields = ', '.join(LABELS[f][0] for f in status['missing'])
    fields_ru = ', '.join(LABELS[f][1] for f in status['missing'])
    body = (f"Please specify {fields} for {status['product']}.\n"
            f"Уточните, пожалуйста: {fields_ru} для «{status['product']}».\n"
            f"Source / Источник: «{status['source_quote']}»")
    key = f"gate:{order_id}:" + ','.join(status['missing'])
    conn.execute("INSERT OR IGNORE INTO outbox (kind, ref_id, subject, body, created_at, "
                 "created_by, idem_key) VALUES ('email', ?, ?, ?, ?, ?, ?)",
                 (order_id, 'Order specifications / Параметры заказа', body, stamp(conn), person_id, key))
    conn.commit()
    oid = conn.execute('SELECT id FROM outbox WHERE idem_key=?', (key,)).fetchone()[0]
    return {'ok': True, 'outbox_id': oid, 'body': body, 'dry_run': True,
            'message': 'Question saved in the dry-run outbox. / Вопрос сохранён в черновиках исходящих.'}


def propose_answer(conn, person_id, order_id, text):
    text = text.strip()
    if not text or len(text) > 2000:
        raise DomainError('Paste the customer answer (up to 2000 characters). / Вставьте ответ клиента (до 2000 знаков).')
    status = check(conn, order_id)
    values = {f: {'value': v, 'quote': text} for f in status['missing']
              if (v := extract(conn, text, f))}
    payload = {'order_id': order_id, 'product_id': status['product_id'],
               'variant_before': status['variant'], 'source': text, 'values': values}
    aid = conn.execute("INSERT INTO agent_actions (kind,payload,model,created_at,for_person) "
                       "VALUES ('gate',?,'offline-catalog',?,?)",
                       (json.dumps(payload, ensure_ascii=False), stamp(conn), person_id)).lastrowid
    conn.commit()
    return {'ok': True, 'id': aid, **payload, 'can_apply': bool(values),
            'message': 'Check the quoted specifications, then confirm. / Проверьте параметры и цитату, затем подтвердите.'}


def apply_answer(conn, person_id, action_id):
    """The confirmation carries only an id; stale answers must be proposed again."""
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = conn.execute("SELECT * FROM agent_actions WHERE id=? AND kind='gate'", (action_id,)).fetchone()
        if row is None or row['for_person'] != person_id:
            raise DomainError('No such proposal of yours. / Такого вашего предложения нет.')
        if row['status'] == 'applied':
            conn.rollback()
            return {'ok': True, 'status': 'already', 'message': 'Already recorded. / Уже записано.'}
        if row['status'] != 'proposed':
            raise DomainError('This proposal is closed. / Предложение закрыто.')
        p = json.loads(row['payload'])
        status = check(conn, p['order_id'])
        if status['cancelled']:
            raise DomainError('Order cancelled. Nothing changed. / Заказ отменён. Ничего не изменилось.')
        if status['variant'] != p['variant_before'] or status['product_id'] != p['product_id']:
            raise DomainError('Order changed. Paste the answer again. / Заказ изменился. Вставьте ответ ещё раз.')
        if conn.execute('SELECT 1 FROM units u JOIN work_events e ON e.unit_id=u.id '
                        'WHERE u.order_id=? LIMIT 1', (p['order_id'],)).fetchone():
            raise DomainError('Work has started; use the order change review. / Работа началась; измените заказ через проверку изменений.')
        if not p['values']:
            raise DomainError('No unambiguous catalog option found. Ask the customer again. / Нет однозначного варианта из каталога. Уточните у клиента.')
        variant = ' · '.join([p['variant_before']] + [f'{LABELS[k][0]}: {v["value"]}' for k, v in p['values'].items()]).strip(' ·')
        conn.execute('UPDATE orders SET variant=? WHERE id=?', (variant, p['order_id']))
        from . import orders
        orders.log(conn, p['order_id'], person_id, 'variant', p['variant_before'], variant,
                   'Customer answer / Ответ клиента: ' + p['source'])
        conn.execute("UPDATE agent_actions SET status='applied',decided_by=?,decided_at=? WHERE id=?",
                     (person_id, stamp(conn), action_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {'ok': True, 'status': 'applied', **check(conn, p['order_id'])}


def seed(conn, rnd):
    """One invented incomplete message makes the protection visible in the demo."""
    row = conn.execute("SELECT id FROM products WHERE name='Harbor armchair'").fetchone()
    if row:
        reference = conn.execute('SELECT MAX(created_at) FROM orders').fetchone()[0]
        oid = domain.create_order(conn, product_id=row[0], qty=1, variant='velvet beige',
                                  customer_id=None, due_date=None, source='gate-demo', commit=False)
        conn.execute('UPDATE orders SET created_at=? WHERE id=?', (reference, oid))
        conn.execute('INSERT INTO gate_sources VALUES (?,?)',
                     (oid, 'One Harbor armchair, velvet beige. / Одно кресло Харбор, велюр беж.'))
        conn.commit()


router = APIRouter()


@router.get('/api/gate/summary')
def summary_route(s: Session = Depends(session), conn=Depends(get_conn)):
    return summary(conn)


@router.get('/api/gate')
def board_route(s: Session = Depends(office), conn=Depends(get_conn)):
    return {**summary(conn), 'lines': [check(conn, oid) for oid in sorted(held_orders(conn))],
            'requirements': [dict(r) for r in conn.execute('SELECT * FROM product_requirements')]}


@router.post('/api/gate/{order_id}/question')
def question_route(order_id: int, s: Session = Depends(office), conn=Depends(get_conn)):
    return question(conn, s.person_id, order_id)


class AnswerIn(BaseModel):
    text: str


@router.post('/api/gate/{order_id}/answer')
def answer_route(order_id: int, body: AnswerIn, s: Session = Depends(office), conn=Depends(get_conn)):
    return propose_answer(conn, s.person_id, order_id, body.text)


@router.post('/api/gate/answers/{action_id}/confirm')
def confirm_route(action_id: int, s: Session = Depends(office), conn=Depends(get_conn)):
    return apply_answer(conn, s.person_id, action_id)
