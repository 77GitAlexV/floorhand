"""A shop's words become reviewable proposals, then learned human corrections."""
import json
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from .. import domain, intake, models
from ..domain import DomainError
from ..models import BudgetStop
from ..web import Session, get_conn, office, worker
from . import marking as mk

SCHEMA = """
CREATE TABLE IF NOT EXISTS slang_aliases (
    id INTEGER PRIMARY KEY,
    phrase TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('product','stage','option','component','phrase')),
    meaning TEXT NOT NULL,
    learned_by INTEGER REFERENCES people(id),
    learned_at TEXT NOT NULL,
    action_id INTEGER REFERENCES agent_actions(id),
    active INTEGER NOT NULL DEFAULT 1,
    deleted_by INTEGER REFERENCES people(id),
    deleted_at TEXT,
    UNIQUE(kind, phrase)
);
CREATE TABLE IF NOT EXISTS slang_alias_revisions (
    id INTEGER PRIMARY KEY,
    alias_id INTEGER NOT NULL REFERENCES slang_aliases(id),
    meaning TEXT NOT NULL,
    learned_by INTEGER NOT NULL REFERENCES people(id),
    learned_at TEXT NOT NULL,
    action_id INTEGER NOT NULL UNIQUE REFERENCES agent_actions(id)
);
-- A cushion count is visible part work, never a completed whole chair or automatic pay.
CREATE TABLE IF NOT EXISTS slang_component_events (
    id INTEGER PRIMARY KEY,
    action_id INTEGER NOT NULL UNIQUE REFERENCES agent_actions(id),
    person_id INTEGER NOT NULL REFERENCES people(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    stage_id INTEGER NOT NULL REFERENCES stages(id),
    component TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK(qty>0),
    work_date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS slang_components_no_update BEFORE UPDATE ON slang_component_events
BEGIN SELECT RAISE(ABORT, 'component events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS slang_components_no_delete BEFORE DELETE ON slang_component_events
BEGIN SELECT RAISE(ABORT, 'component events are append-only'); END;
"""

OPTIONS = ['curved legs', 'straight legs']
COMPONENTS = {'seat cushions': 'сидушки'}
NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
           'a': 1, 'pair': 2, 'couple': 2, 'один': 1, 'одна': 1, 'одно': 1,
           'два': 2, 'две': 2, 'три': 3, 'четыре': 4, 'пять': 5, 'шесть': 6,
           'пара': 2, 'пару': 2}
FILLER = set('done ready finished made for of the on at with please just now I have '
             'готов готово готовы сделал сделала сделаны на для к по с со у уже сегодня '
             'шт штук штуки pcs pieces piece'.casefold().split())
UNSAFE = re.compile(r'\b(?:not|no|never|maybe|tomorrow|undo|нет|не|завтра|потом|кажется|отмени|если)\b|\?', re.I)


def norm(text):
    return ' '.join(re.findall(r'[a-zа-яё0-9]+', text.casefold().replace('ё', 'е')))


def aliases(conn):
    return [dict(r, meaning=json.loads(r['meaning'])) for r in conn.execute(
        'SELECT * FROM slang_aliases WHERE active=1 ORDER BY length(phrase) DESC, id')]


def mentions_dictionary(conn, text):
    words = ' ' + norm(text) + ' '
    return any(a['kind'] in ('product', 'option', 'component', 'phrase') and
               ' ' + a['phrase'] + ' ' in words for a in aliases(conn))


def catalog(conn):
    """A worker sees identifiers and words, never catalog prices."""
    from .gate import choices
    return {'products': [dict(r) for r in conn.execute('SELECT id,name,kind FROM products')],
            'stages': [dict(r) for r in conn.execute('SELECT id,code,name_en,name_ru FROM stages')],
            'options': OPTIONS + choices(conn, 'leg_finish') + choices(conn, 'fabric'),
            'components': list(COMPONENTS)}


def offline(conn, text, stage_id=None):
    """Consume known words only. Ambiguity and negation are a request to check."""
    if UNSAFE.search(text):
        return None
    words = norm(text)
    dictionary = aliases(conn)
    learned = [a for a in dictionary if a['kind'] == 'phrase' and a['phrase'] == words]
    if learned:
        return learned[0]['meaning']
    found = {}
    remaining = ' ' + words + ' '
    for alias in dictionary:
        if alias['kind'] == 'phrase':
            continue
        token = ' ' + alias['phrase'] + ' '
        if token not in remaining:
            continue
        kind = alias['kind']
        value = alias['meaning']['value']
        found.setdefault(kind, []).append(value)
        remaining = remaining.replace(token, ' ')
    for key in found:
        if len(set(found[key])) != 1:
            return None
    if not found.get('product'):
        return None
    counts, unknown = [], []
    for word in remaining.split():
        if word.isdigit() or word in NUMBERS:
            counts.append(int(word) if word.isdigit() else NUMBERS[word])
        elif word not in FILLER:
            unknown.append(word)
    if unknown or len(counts) != 1 or not 1 <= counts[0] <= 100:
        return None
    return {'product_id': found['product'][0], 'qty': counts[0],
            'stage_id': found.get('stage', [stage_id])[0],
            'option': found.get('option', [None])[0],
            'component': found.get('component', [None])[0]}


def validate(conn, meaning, stage_id):
    cat = catalog(conn)
    if (type(meaning.get('product_id')) is not int or
            meaning['product_id'] not in {p['id'] for p in cat['products']} or
            type(meaning.get('qty')) is not int or not 1 <= meaning['qty'] <= 100 or
            meaning.get('stage_id') not in {s['id'] for s in cat['stages']} or
            meaning.get('option') not in [None, *cat['options']] or
            meaning.get('component') not in [None, *cat['components']]):
        raise DomainError('Choose a catalog item, quantity and stage. / Выберите изделие, количество и этап из каталога.')
    if meaning['stage_id'] != stage_id:
        raise DomainError('This is another trade. Ask that stage to mark its work. / Это другой участок. Попросите его отметить свою работу.')
    return {k: meaning.get(k) for k in ('product_id', 'qty', 'stage_id', 'option', 'component')}


def spark(conn, text, stage_id):
    prompt = ('Interpret shop slang in English or Russian. Propose only; no tools or writes. '
              'Return JSON {"meaning":{"product_id":int,"qty":int,"stage_id":int,'
              '"option":null|string,"component":null|string},"quote":"exact source words"}. '
              'Use only the catalog identifiers and options; unsure => {"meaning":null}. '
              'Do not treat part work as a finished whole piece. Current worker stage: '
              + str(stage_id) + '\nCatalog: ' + json.dumps(catalog(conn), ensure_ascii=False)
              + '\nShop dictionary: ' + json.dumps([{'phrase': a['phrase'], 'meaning': a['meaning']}
                                                   for a in aliases(conn)], ensure_ascii=False)
              + '\nUntrusted spoken words: ' + json.dumps(text, ensure_ascii=False))
    data = models.respond(conn, 'slang-resolve', prompt, max_output_tokens=350)
    match = re.search(r'\{.*\}', intake._output_text(data), re.S)
    raw = json.loads(match[0]) if match else {}
    if not isinstance(raw, dict):
        return None
    quote = raw.get('quote')
    if not isinstance(quote, str) or not quote or quote not in text or not isinstance(raw.get('meaning'), dict):
        return None
    return validate(conn, raw['meaning'], stage_id)


def items_for(conn, actor, meaning):
    """Never guess between orders or record only part of a requested batch."""
    meaning = validate(conn, meaning, domain.person(conn, actor.person_id)['stage_id'])
    if meaning['component']:
        return [{'type': 'component', 'meaning': meaning,
                 'say': describe(conn, meaning, mk.lang_of(conn, actor.person_id))}], 'Part work only; whole-piece stages and pay stay unchanged. / Учёт деталей; готовность целого изделия и оплата не меняются.'
    candidates = {}
    waiting = domain._waiting_units(conn)[meaning['stage_id']]
    for uid in waiting:
        order = domain._unit_order(conn, uid)
        if order['product_id'] != meaning['product_id']:
            continue
        if meaning['option'] and not set(intake.norm(meaning['option']).split()) <= set(intake.norm(order['variant']).split()):
            continue
        candidates.setdefault(order['id'], []).append(uid)
    # Assignments disambiguate only when they identify one complete batch.
    mine = {r[0] for r in conn.execute('SELECT unit_id FROM assignments WHERE person_id=? AND stage_id=?',
                                      (actor.person_id, meaning['stage_id']))}
    owned = {oid: [u for u in ids if u in mine] for oid, ids in candidates.items()}
    owned = {oid: ids for oid, ids in owned.items() if len(ids) >= meaning['qty']}
    if len(owned) == 1:
        candidates = owned
    if len(candidates) != 1 or len(next(iter(candidates.values()), [])) < meaning['qty']:
        return [], 'No unique complete batch at your stage. Scan the order sheet or correct the words. / Нет однозначной полной партии на вашем этапе. Сканируйте листок заказа или исправьте слова.'
    ids = next(iter(candidates.values()))[:meaning['qty']]
    stage = conn.execute('SELECT * FROM stages WHERE id=?', (meaning['stage_id'],)).fetchone()
    lang = mk.lang_of(conn, actor.person_id)
    return [{'type': 'mark', 'unit_id': u, 'label': domain.unit_label(conn, u),
             'say': domain.unit_label(conn, u) + ' ' + mk._sname(stage, lang)} for u in ids], ''


def describe(conn, meaning, lang):
    if not meaning:
        return mk.T(lang, 'I need help understanding. Choose the item below.', 'Нужно уточнение. Выберите изделие ниже.')
    product = conn.execute('SELECT name FROM products WHERE id=?', (meaning['product_id'],)).fetchone()[0]
    stage = conn.execute('SELECT * FROM stages WHERE id=?', (meaning['stage_id'],)).fetchone()
    detail = meaning.get('component') or meaning.get('option') or ''
    if lang == 'ru':
        detail = {'curved legs': 'кривые ножки', 'straight legs': 'прямые ножки', **COMPONENTS}.get(detail, detail)
    return mk.T(lang, f"CALC: {meaning['qty']} × {product}, {detail}, {stage['name_en']}.",
                f"РАСЧЁТ: {meaning['qty']} × {product}, {detail}, {stage['name_ru']}.")


def propose(conn, actor, text, *, heard_by='browser-speech', created_at=None, allow_model=True):
    text = text.strip()
    if not text or len(text) > 500:
        raise DomainError('Say a short phrase (up to 500 characters). / Скажите короткую фразу (до 500 знаков).')
    stage_id = domain.person(conn, actor.person_id)['stage_id']
    if stage_id is None:
        raise DomainError('Ask the office to set your stage. / Попросите офис указать ваш участок.')
    lang = mk.lang_of(conn, actor.person_id)
    if UNSAFE.search(text):
        return {'ok': False, 'status': 'nothing', 'can_apply': 0,
                'message': mk.T(lang, 'Nothing recorded. Say only work already completed.',
                               'Ничего не записано. Скажите только о завершённой работе.')}
    meaning, model = offline(conn, text, stage_id), 'offline-dictionary'
    if not meaning and allow_model:
        try:
            meaning = spark(conn, text, stage_id)
            model = models.SPARK if meaning else model
        except (BudgetStop, ValueError, KeyError, TypeError, DomainError):
            pass
    items, problem = [], ''
    if meaning:
        try:
            meaning = validate(conn, meaning, stage_id)
            items, problem = items_for(conn, actor, meaning)
        except DomainError as exc:
            problem = str(exc)
    payload = {'heard': text, 'method': 'voice', 'created_at': created_at, 'stage_id': stage_id,
               'items': items, 'slang': {'meaning': meaning, 'corrected': False}}
    aid = conn.execute("INSERT INTO agent_actions(kind,payload,model,created_at,for_person) "
                       "VALUES ('marking',?,?,?,?)", (json.dumps(payload, ensure_ascii=False),
                       heard_by + '+' + model, mk._iso(mk._now(conn)), actor.person_id)).lastrowid
    conn.commit()
    return {'ok': True, 'status': 'proposal', 'id': aid, 'heard': text, 'meaning': meaning,
            'items': items, 'can_apply': len(items), 'model': model,
            'message': describe(conn, meaning, lang) + (' ' + problem if problem else '')}


def fix(conn, actor, action_id, meaning):
    row = conn.execute("SELECT * FROM agent_actions WHERE id=? AND kind='marking'", (action_id,)).fetchone()
    if row is None or row['for_person'] != actor.person_id or row['status'] != 'proposed':
        raise DomainError('No open proposal of yours. / Нет вашего открытого предложения.')
    payload = json.loads(row['payload'])
    if 'slang' not in payload:
        raise DomainError('Open hands-free marking to correct slang. / Откройте голосовые отметки для исправления словаря.')
    meaning = validate(conn, meaning, domain.person(conn, actor.person_id)['stage_id'])
    items, problem = items_for(conn, actor, meaning)
    payload['items'] = items
    payload['slang'] = {'meaning': meaning, 'corrected': True}
    conn.execute('UPDATE agent_actions SET payload=? WHERE id=? AND status=\'proposed\'',
                 (json.dumps(payload, ensure_ascii=False), action_id))
    conn.commit()
    return {'ok': True, 'id': action_id, 'meaning': meaning, 'items': items, 'can_apply': len(items),
            'message': describe(conn, meaning, mk.lang_of(conn, actor.person_id)) + ' ' + problem}


def learn(conn, actor, action_id, payload, results):
    slang = payload.get('slang')
    if not slang or not slang['corrected'] or not results or not all(r['ok'] for r in results):
        return
    # Deletion is a human decision too: a later confirm never resurrects a deleted alias.
    conn.execute('INSERT OR IGNORE INTO slang_aliases '
                 '(phrase,kind,meaning,learned_by,learned_at,action_id) VALUES (?,\'phrase\',?,?,?,?)',
                 (norm(payload['heard']), json.dumps(slang['meaning'], ensure_ascii=False),
                  actor.person_id, mk._iso(mk._now(conn)), action_id))
    row = conn.execute("SELECT id,active FROM slang_aliases WHERE kind='phrase' AND phrase=?", (norm(payload['heard']),)).fetchone()
    if row and row['active']:
        conn.execute('UPDATE slang_aliases SET meaning=?,learned_by=?,learned_at=?,action_id=? WHERE id=?',
                     (json.dumps(slang['meaning'], ensure_ascii=False), actor.person_id,
                      mk._iso(mk._now(conn)), action_id, row['id']))
        conn.execute('INSERT OR IGNORE INTO slang_alias_revisions '
                     '(alias_id,meaning,learned_by,learned_at,action_id) VALUES (?,?,?,?,?)',
                     (row['id'], json.dumps(slang['meaning'], ensure_ascii=False), actor.person_id,
                      mk._iso(mk._now(conn)), action_id))


def record_component(conn, actor, action_id, meaning, created_at):
    lang = mk.lang_of(conn, actor.person_id)
    day = mk.work_day(conn, actor.person_id, created_at, None, lang)
    cur = conn.execute('INSERT OR IGNORE INTO slang_component_events '
                       '(action_id,person_id,product_id,stage_id,component,qty,work_date,created_at) '
                       'VALUES (?,?,?,?,?,?,?,?)',
                       (action_id, actor.person_id, meaning['product_id'], meaning['stage_id'],
                        meaning['component'], meaning['qty'], day.work_date, mk._iso(mk._now(conn))))
    conn.commit()
    return {'ok': True, 'status': 'recorded' if cur.rowcount else 'already',
            'message': mk.T(lang, 'Recorded part work. ', 'Записана работа над деталями. ')
                       + describe(conn, meaning, lang) + mk.T(lang,
                           ' Whole-piece stages and pay stay unchanged.',
                           ' Готовность целого изделия и оплата не меняются.')}


def seed(conn, rnd):
    seed_at = conn.execute('SELECT MAX(created_at) FROM orders').fetchone()[0] or mk._iso(mk._now(conn))
    def add(phrase, kind, value):
        conn.execute('INSERT OR IGNORE INTO slang_aliases(phrase,kind,meaning,learned_at) VALUES (?,?,?,?)',
                     (norm(phrase), kind, json.dumps({'value': value}), seed_at))
    names = {'Harbor armchair': ['harbor', 'harbors', 'харбор', 'харбора', 'харборы', 'харборов'],
             'Harbor rocking chair': ['harbor rocker', 'harbor rockers', 'качалка харбор', 'качалки харбор'],
             'Cozy armchair': ['cozy', 'cozies', 'кози'], 'Round pouf': ['round pouf', 'round poufs', 'круглый пуф', 'круглых пуфа'],
             'Square pouf': ['square pouf', 'square poufs', 'квадратный пуф', 'квадратных пуфа']}
    for p in conn.execute('SELECT id,name FROM products'):
        for phrase in [p['name'], p['name'] + 's', *names.get(p['name'], [])]:
            add(phrase, 'product', p['id'])
    stages = {'cut': ['cut', 'cutting', 'раскроил', 'раскроила', 'раскрой'],
              'sew': ['sewn', 'sewed', 'stitched', 'сшил', 'сшила', 'пошил', 'шитье'],
              'uph': ['upholstered', 'обил', 'обтянул', 'обивка'],
              'asm': ['assembled', 'assembly', 'built', 'собрал', 'собрала', 'сборка'],
              'pack': ['packed', 'boxed', 'упаковал', 'упаковала', 'упаковка']}
    for s in domain.stages(conn):
        for phrase in stages.get(s['code'], []):
            add(phrase, 'stage', s['id'])
    for phrase in ['curved legs', 'кривых ногах', 'кривые ноги', 'кривые ножки']:
        add(phrase, 'option', 'curved legs')
    for phrase in ['straight legs', 'прямых ногах', 'прямые ножки']:
        add(phrase, 'option', 'straight legs')
    for phrase in ['seat cushions', 'сидушки', 'сидушек', 'сидушку']:
        add(phrase, 'component', 'seat cushions')
    for option in catalog(conn)['options']:
        add(option, 'option', option)
    for phrase, option in [('орех', 'walnut'), ('дуб', 'oak'), ('черные ножки', 'black'),
                           ('натуральные ножки', 'natural'), ('бежевый велюр', 'velvet beige')]:
        if option in catalog(conn)['options']:
            add(phrase, 'option', option)
    conn.commit()
    # One complete, assigned batch makes the headline demo phrase reproducible.
    product = conn.execute("SELECT id FROM products WHERE name='Harbor armchair'").fetchone()
    if product:
        oid = domain.create_order(conn, product_id=product[0], qty=2, customer_id=None,
                                  due_date=None, variant='velvet beige · walnut · curved legs',
                                  source='slang-demo')
        conn.execute('UPDATE orders SET created_at=? WHERE id=?', (seed_at, oid))
        units = [r[0] for r in conn.execute('SELECT id FROM units WHERE order_id=? ORDER BY seq', (oid,))]
        for code in ('cut', 'sew', 'uph'):
            person = conn.execute('SELECT p.id,p.stage_id FROM people p JOIN stages s ON s.id=p.stage_id '
                                  'WHERE s.code=? ORDER BY p.id LIMIT 1', (code,)).fetchone()
            if person:
                for uid in units:
                    conn.execute("INSERT INTO work_events(kind,unit_id,stage_id,person_id,work_date,created_at,method) "
                                 "VALUES ('done',?,?,?,?,?,'button')",
                                 (uid, person['stage_id'], person['id'], seed_at[:10], seed_at))
        person = conn.execute("SELECT p.id,p.stage_id FROM people p JOIN stages s ON s.id=p.stage_id "
                              "WHERE s.code='asm' ORDER BY p.id LIMIT 1").fetchone()
        if person:
            conn.executemany('INSERT INTO assignments VALUES (?,?,?)',
                             [(uid, person['stage_id'], person['id']) for uid in units])
        conn.commit()


router = APIRouter()


class TextIn(BaseModel):
    text: str
    created_at: str | None = None


@router.get('/api/slang/catalog')
def catalog_route(s: Session = Depends(worker), conn=Depends(get_conn)):
    return {'ok': True, **catalog(conn)}


@router.post('/api/slang/propose')
def propose_route(body: TextIn, s: Session = Depends(worker), conn=Depends(get_conn)):
    return propose(conn, mk.Actor.of(s, conn), body.text, created_at=body.created_at)


class FixIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_id: int
    qty: int
    stage_id: int
    option: str | None = None
    component: str | None = None


@router.post('/api/slang/{action_id}/fix')
def fix_route(action_id: int, body: FixIn, s: Session = Depends(worker), conn=Depends(get_conn)):
    return fix(conn, mk.Actor.of(s, conn), action_id, body.model_dump())


class DecideIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    yes: bool | None = None
    spoken: str | None = None


@router.post('/api/slang/{action_id}/decide')
def decide_route(action_id: int, body: DecideIn, s: Session = Depends(worker), conn=Depends(get_conn)):
    yes = body.yes
    if body.spoken is not None:
        word = norm(body.spoken)
        if word not in ('yes', 'да', 'no', 'нет'):
            raise DomainError('Say yes or no. / Скажите да или нет.')
        yes = word in ('yes', 'да')
    if yes is None:
        raise DomainError('Confirm yes or no. / Подтвердите: да или нет.')
    return mk.decide(conn, mk.Actor.of(s, conn), action_id, yes)


@router.get('/api/slang/aliases')
def aliases_route(s: Session = Depends(office), conn=Depends(get_conn)):
    people = {r['id']: r['name'] for r in conn.execute('SELECT id,name FROM people')}
    rows = aliases(conn)
    for row in rows:
        row['learned_by_name'] = people.get(row['learned_by'])
        if row['kind'] == 'phrase':
            row['description'] = describe(conn, row['meaning'], mk.lang_of(conn, s.person_id))
    return {'ok': True, 'aliases': rows,
            'components': [dict(r) for r in conn.execute(
                'SELECT e.qty,e.work_date,e.component,p.name AS product,w.name AS person '
                'FROM slang_component_events e JOIN products p ON p.id=e.product_id '
                'JOIN people w ON w.id=e.person_id ORDER BY e.id DESC LIMIT 50')]}


@router.delete('/api/slang/aliases/{alias_id}')
def delete_route(alias_id: int, s: Session = Depends(office), conn=Depends(get_conn)):
    conn.execute('UPDATE slang_aliases SET active=0,deleted_by=?,deleted_at=? WHERE id=? AND active=1',
                 (s.person_id, mk._iso(mk._now(conn)), alias_id))
    conn.commit()
    return {'ok': True, 'message': 'Alias removed from use; history kept. / Слово удалено из словаря; история сохранена.'}
