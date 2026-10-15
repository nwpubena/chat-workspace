"""Read-only keyword and local ChatLab semantic evidence retrieval."""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import urllib.request
from convert_pywxdump import ROOT, SESSION, TZ


def api(endpoint, body=None):
    url = 'http://127.0.0.1:3110/_web/ai/semantic-index/' + endpoint
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    # Never send local private queries through environment-configured proxies.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=60) as response:
        return json.load(response)


def connect():
    path = ROOT.parent / 'chatlab-data/databases' / (SESSION + '.db')
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def evidence(row):
    return {'id': row['id'], 'platform_message_id': row['platform_message_id'],
            'time': datetime.fromtimestamp(row['ts'], TZ).isoformat(),
            'sender': row['sender_account_name'], 'content': row['content'],
            'type': row['type'], 'reply_to_message_id': row['reply_to_message_id']}


def date_bound(value, inclusive_end=False):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    if len(value) == 10 and inclusive_end:
        parsed += timedelta(days=1)
        return int(parsed.timestamp()) - 1
    return int(parsed.timestamp())


def context_rows(conn, row, count):
    if not count:
        return []
    # Use (timestamp, id), not id arithmetic: same-second messages and gaps exist.
    before = conn.execute('SELECT * FROM message WHERE (ts,id)<(?,?) ORDER BY ts DESC,id DESC LIMIT ?',
                          (row['ts'], row['id'], count)).fetchall()
    after = conn.execute('SELECT * FROM message WHERE (ts,id)>(?,?) ORDER BY ts,id LIMIT ?',
                         (row['ts'], row['id'], count)).fetchall()
    return [{**evidence(r), 'evidence_role': 'context'} for r in [*reversed(before), *after]]


def search(query, mode='keyword', limit=5, sender=None, since=None, until=None, context=0):
    if not isinstance(query, str) or not query.strip():
        raise ValueError('query must not be blank')
    if mode not in ('keyword', 'semantic') or not 1 <= limit <= 20 or not 0 <= context <= 10:
        raise ValueError('Invalid mode, limit (1..20) or context (0..10)')
    if sender not in (None, '我', '目标联系人'):
        raise ValueError('sender must be 我 or 目标联系人')
    start, end = date_bound(since), date_bound(until, inclusive_end=True)
    if start is not None and end is not None and start > end:
        raise ValueError('since must not be after until')
    if mode == 'semantic' and (sender is not None or since is not None or until is not None or context):
        raise ValueError('This ChatLab semantic endpoint returns candidate contexts without server-side sender/date filters; use keyword mode for exhaustive filtered searches')
    conn = connect()
    try:
        if mode == 'keyword':
            escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            conditions = ["content LIKE ? ESCAPE '\\'", 'type NOT IN (80,81)']
            params = ['%' + escaped + '%']
            for clause, value in [('sender_account_name=?', sender), ('ts>=?', start), ('ts<=?', end)]:
                if value is not None:
                    conditions.append(clause)
                    params.append(value)
            where = ' AND '.join(conditions)
            total = conn.execute('SELECT COUNT(*) FROM message WHERE ' + where, params).fetchone()[0]
            rows = conn.execute('SELECT * FROM message WHERE ' + where + ' ORDER BY ts DESC,id DESC LIMIT ?',
                                (*params, limit)).fetchall()
            return {'mode': mode, 'query': query, 'partial': False, 'total_matches': total,
                    'filters': {'sender': sender, 'since': since, 'until': until},
                    'truncated': total > len(rows),
                    'messages': [{**evidence(r), 'evidence_role': 'match',
                                  'context': context_rows(conn, r, context)} for r in rows]}
        response = api('search', {'sessionId': SESSION, 'query': query, 'finalTopK': limit})
        blocks = []
        for block in response.get('blocks', []):
            items = []
            for msg in block['messages']:
                row = conn.execute('SELECT * FROM message WHERE id=?', (msg['id'],)).fetchone()
                if row is not None:
                    items.append({**evidence(row), 'evidence_role': 'candidate_context'})
            blocks.append({'score': block['score'], 'messages': items})
        return {'mode': mode, 'query': query, 'available': response.get('available'),
                'partial': response.get('partial', True), 'coverage': response.get('coverage', 0),
                'reason': response.get('reason'), 'blocks': blocks,
                'note': 'Semantic results are candidate conversation contexts, not proof that every message matches the query.'}
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('query')
    parser.add_argument('--mode', choices=('keyword', 'semantic'), default='keyword')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--sender', choices=('我', '目标联系人'))
    parser.add_argument('--since', help='Keyword mode: YYYY-MM-DD or ISO datetime; defaults to Beijing time')
    parser.add_argument('--until', help='Keyword mode: YYYY-MM-DD includes the entire day')
    parser.add_argument('--context', type=int, default=0, help='Keyword mode: up to 10 messages before and after each hit')
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        parser.error('--limit must be between 1 and 20')
    try:
        result = search(args.query, args.mode, args.limit, args.sender, args.since, args.until, args.context)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
