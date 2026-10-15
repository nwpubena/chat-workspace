import unittest
from convert_pywxdump import OWNER, TARGET, convert, timestamp


def row(**overrides):
    data = dict(id=1, MsgSvrID='11', type_name='文本', is_sender=0, talker=TARGET,
                room_name=TARGET, msg='你好', src='', extra={}, CreateTime='2026-06-06 21:08:11')
    data.update(overrides)
    return data


class ConversionTests(unittest.TestCase):
    def test_timezone(self):
        self.assertEqual(timestamp('1970-01-01 08:00:00'), 0)

    def test_directions(self):
        self.assertEqual(convert([row()], {})[0]['sender_name'], '目标联系人')
        self.assertEqual(convert([row(talker=OWNER, is_sender=1)], {})[0]['sender_name'], '我')

    def test_wrong_target_rejected(self):
        with self.assertRaises(ValueError):
            convert([row(room_name='other')], {})

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            convert([row(), row()], {})

    def test_system_not_peer(self):
        self.assertEqual(convert([row(type_name='系统通知')], {})[0]['sender_id'], 'SYSTEM')

    def test_poke_as_system_event(self):
        msg = convert([row(type_name='拍一拍')], {})[0]
        self.assertEqual((msg['sender_id'], msg['type']), ('SYSTEM', 80))

    def test_quote_separated_and_linked(self):
        reply = row(id=2, MsgSvrID='12', talker=OWNER, is_sender=1, type_name='引用回复',
                    CreateTime='2026-06-06 21:09:11', msg='嗨\n\n[引用](2026-06-06 21:08:11)目标联系人:你好')
        messages = convert([row(), reply], {TARGET: {'nickname': '目标联系人'}})
        self.assertEqual(messages[1]['text'], '嗨')
        self.assertEqual(messages[1]['quote']['message_id'], 'wx:11')

    def test_ambiguous_quote_not_linked(self):
        reply = row(MsgSvrID='13', type_name='引用回复', CreateTime='2026-06-06 21:09:11',
                    msg='嗨\n\n[引用](2026-06-06 21:08:11)目标联系人:你好')
        messages = convert([row(), row(MsgSvrID='12'), reply], {TARGET: {'nickname': '目标联系人'}})
        self.assertIsNone(messages[-1]['quote']['message_id'])

    def test_zero_id_stable_not_sequential(self):
        self.assertEqual(convert([row(MsgSvrID='0')], {})[0]['id'],
                         convert([row(MsgSvrID='0', id=999)], {})[0]['id'])

    def test_reexport_only_new_ids_are_added(self):
        before = convert([row()], {})
        after = convert([row(id=99), row(id=100, MsgSvrID='12',
                        CreateTime='2026-06-06 21:09:11', msg='新增消息')], {})
        known = {m['id'] for m in before}
        self.assertEqual([m['id'] for m in after if m['id'] not in known], ['wx:12'])

    def test_backfilled_older_message_not_lost_to_time_cutoff(self):
        before = convert([row()], {})
        after = convert([row(MsgSvrID='10', CreateTime='2026-06-05 12:00:00'), row()], {})
        self.assertEqual({m['id'] for m in after} - {m['id'] for m in before}, {'wx:10'})

    def test_same_text_different_platform_ids_remain_distinct(self):
        result = convert([row(), row(id=2, MsgSvrID='12')], {})
        self.assertEqual([m['id'] for m in result], ['wx:11', 'wx:12'])

    def test_missing_quote_author_preserved(self):
        reply = row(type_name='引用回复', msg='嗨\n\n[引用](2026-06-06 21:08:11):你好')
        msg = convert([reply], {})[0]
        self.assertEqual(msg['text'], '嗨')
        self.assertEqual(msg['quote']['text'], '你好')
        self.assertIsNone(msg['quote']['message_id'])


if __name__ == '__main__':
    unittest.main()
