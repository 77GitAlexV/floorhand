"""Repeat the merged-feature GET audit against an already seeded, disposable DB."""
import ast
from contextlib import closing
import json
from pathlib import Path
import re
import struct
import zlib

from fastapi.testclient import TestClient

from floorhand import db
from floorhand.main import app


WORKER_STATUS = {
    '/api/ripple/cards': 403,
    '/api/rush/catalog': 403,
    '/api/slang/catalog': 200,
    '/api/slang/aliases': 403,
    '/api/gate/summary': 200,
    '/api/gate': 403,
    '/api/channels/overview': 403,
    '/api/shelf/items': 403,
    '/api/shelf/{action_id}': 403,
    '/api/live/snapshot': 403,
}
MONEY = re.compile(r'(?:^|_)(?:money|currency|prices?|rates?|costs?|amount|pay|paid|payout|'
                   r'wages?|salary|revenue|margin|surcharge|penalties|cash|subtotal|total)(?:_|$)')


def money_fields(value):
    if isinstance(value, dict):
        return [k for k in value if MONEY.search(k)] + [
            k for child in value.values() for k in money_fields(child)]
    if isinstance(value, list):
        return [k for child in value for k in money_fields(child)]
    return []


def declared_gets():
    """Keep the smoke exhaustive when these feature modules gain a GET route."""
    features = Path(__file__).resolve().parents[1] / 'floorhand' / 'features'
    routes = set()
    for name in ('ripple', 'rush', 'slang', 'gate', 'channels', 'shelf_count', 'simulator'):
        tree = ast.parse((features / f'{name}.py').read_text(encoding='utf-8'))
        prefix = ''
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                if getattr(node.value.func, 'id', '') == 'APIRouter':
                    prefix = next((ast.literal_eval(k.value) for k in node.value.keywords
                                   if k.arg == 'prefix'), '')
        for node in tree.body:
            for dec in getattr(node, 'decorator_list', []):
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == 'get'):
                    path = prefix + ast.literal_eval(dec.args[0])
                    if name != 'simulator' or path == '/api/live/snapshot':
                        routes.add(path)
    return routes


def demo_photo():
    """An invented black square on white, encoded without optional packages."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = b''.join(b'\0' + bytes(0 if 8 <= x < 24 and 8 <= y < 24 else 255
                                  for x in range(32)) for y in range(32))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 32, 32, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def check_get_routes():
    assert declared_gets() == set(WORKER_STATUS)
    with closing(db.connect()) as conn:
        assert conn.execute('SELECT role FROM people WHERE id=30').fetchone()[0] == 'owner'
        assert conn.execute('SELECT role FROM people WHERE id=1').fetchone()[0] == 'worker'
        before = [tuple(r) for r in conn.execute('SELECT id,qty FROM shelf_items ORDER BY id')]
    evidence = []
    with TestClient(app) as client:
        assert client.post('/api/login', json={'person_id': 30}).status_code == 200
        assert client.post('/api/slang/propose', json={'text': 'two demo parts'}).status_code == 403
        offer = client.post('/api/shelf/count', files={'image': ('demo.png', demo_photo(), 'image/png')},
                            data={'prompt': 'demo part', 'item_id': before[0][0]})
        assert offer.status_code == 200, offer.text
        action_id = offer.json()['id']
        for person, role in ((30, 'owner'), (1, 'worker')):
            assert client.post('/api/login', json={'person_id': person}).status_code == 200
            for template, worker_status in WORKER_STATUS.items():
                path = template.replace('{action_id}', str(action_id))
                response = client.get(path)
                expected = 200 if role == 'owner' else worker_status
                assert response.status_code == expected, (role, path, response.text)
                payload = response.json()
                assert payload, (role, path)
                if role == 'owner':
                    field = {
                        '/api/ripple/cards': 'cards', '/api/rush/catalog': 'products',
                        '/api/slang/catalog': 'products', '/api/slang/aliases': 'aliases',
                        '/api/gate/summary': 'held_lines', '/api/gate': 'lines',
                        '/api/channels/overview': 'items', '/api/shelf/items': 'items',
                        '/api/shelf/{action_id}': 'count', '/api/live/snapshot': 'stages',
                    }[template]
                    assert payload[field], (path, field)
                if role == 'worker':
                    assert not money_fields(payload), (path, money_fields(payload))
                evidence.append({'role': role, 'person_id': person, 'path': path,
                                 'status': response.status_code, 'data': payload})
        assert client.post('/api/logout').status_code == 200
        for template in WORKER_STATUS:
            path = template.replace('{action_id}', str(action_id))
            assert client.get(path).status_code == 401, path
    with closing(db.connect()) as conn:
        assert before == [tuple(r) for r in conn.execute('SELECT id,qty FROM shelf_items ORDER BY id')]
        assert conn.execute('SELECT status FROM agent_actions WHERE id=?', (action_id,)).fetchone()[0] == 'proposed'
    return evidence


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    results = check_get_routes()
    if args.output:
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'FACT: {len(results)} owner/worker GET checks passed; guests refused; no worker money; stock unchanged.')
