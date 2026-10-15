import sqlite3
import unittest
from unittest.mock import patch
from search_wechat import date_bound, search


def fixture():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE message(id INTEGER PRIMARY KEY,platform_message_id TEXT,ts INTEGER,sender_account_name TEXT,type INTEGER,content TEXT,reply_to_message_id TEXT)')
    start = date_bound('2026-09-01')
    rows = [(1, 'a', start - 1, '目标联系人', 0, '吃饭', None),
            (3, 'b', start, '我', 0, '去吃饭', None),
            (4, 'c', start, '目标联系人', 0, '吃饭吧', None),
            (8, 'd', start + 1, '系统消息', 80, '吃饭通知', None),
            (9, 'e', start + 86399, '目标联系人', 0, '100%_完成', None),
            (10, 'f', start + 86400, '目标联系人', 0, '下次吃饭', None)]
    conn.executemany('INSERT INTO message VALUES(?,?,?,?,?,?,?)', rows)
    return conn


class SearchTests(unittest.TestCase):
    def lookup(self, *args, **kwargs):
        with patch('search_wechat.connect', side_effect=fixture):
            return search(*args, **kwargs)

    def test_keyword_exact_scope(self):
        r = self.lookup('吃饭', sender='目标联系人', since='2026-09-01', until='2026-09-01')
        self.assertEqual(r['total_matches'], 1)
        self.assertEqual(r['messages'][0]['id'], 4)

    def test_wildcards_are_literal(self):
        self.assertEqual(self.lookup('%_')['total_matches'], 1)
        self.assertEqual(self.lookup("' OR 1=1 --")['total_matches'], 0)

    def test_system_excluded(self):
        self.assertEqual(self.lookup('通知')['total_matches'], 0)

    def test_context_not_hit(self):
        r = self.lookup('吃饭吧', context=1)
        self.assertEqual([m['id'] for m in r['messages'][0]['context']], [3, 8])
        self.assertTrue(all(m['evidence_role'] == 'context' for m in r['messages'][0]['context']))

    def test_truncation_reported(self):
        r = self.lookup('吃饭', limit=1)
        self.assertTrue(r['truncated'])
        self.assertEqual(r['total_matches'], 4)

    def test_date_end_inclusive(self):
        self.assertEqual(date_bound('2026-09-01', True) - date_bound('2026-09-01'), 86399)
        self.assertEqual(self.lookup('完成', until='2026-09-01')['total_matches'], 1)

    def test_invalid_inputs(self):
        for kwargs in ({'limit': 0}, {'context': 11}, {'sender': 'other'},
                       {'since': '2026-09-02', 'until': '2026-09-01'}):
            with self.assertRaises(ValueError):
                self.lookup('吃饭', **kwargs)
        with self.assertRaises(ValueError):
            self.lookup(' ')

    def test_semantic_filters_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            self.lookup('吃饭', mode='semantic', sender='目标联系人')

    def test_semantic_partial_preserved(self):
        response = {'available': True, 'partial': True, 'coverage': .2,
                    'blocks': [{'score': .5, 'messages': [{'id': 4}]}]}
        with patch('search_wechat.api', return_value=response):
            r = self.lookup('吃饭', mode='semantic')
        self.assertTrue(r['partial'])
        self.assertEqual(r['coverage'], .2)
        self.assertEqual(r['blocks'][0]['messages'][0]['platform_message_id'], 'c')
        self.assertEqual(r['blocks'][0]['messages'][0]['evidence_role'], 'candidate_context')


if __name__ == '__main__':
    unittest.main()
