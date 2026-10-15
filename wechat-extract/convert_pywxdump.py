"""Convert native PyWxDump JSON; never access WeChat or decrypt databases."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
OWNER = 'wxid_your_account'
TARGET = 'wxid_your_contact'
SESSION = 'wechat_example_session'
TZ = timezone(timedelta(hours=8))
TYPE_MAP = {'文本': 0, '图片': 1, '语音': 2, '视频': 3, '文件': 4,
            '动画表情': 5, '(分享)卡片式链接': 7, '位置': 8, '位置共享': 8,
            '转账': 21, '拍一拍': 80, '语音通话': 23, '引用回复': 25,
            '合并转发的聊天记录': 26, '系统通知': 80, '消息撤回57': 81}
QUOTE = re.compile(r'\A(.*?)\n\n\[引用\]\(([^)]*)\)([^:\n]*):(.*)\Z', re.S)


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


def timestamp(value):
    return int(datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ).timestamp())


def convert(rows, users):
    aliases = defaultdict(set)
    for uid in (OWNER, TARGET):
        for key in ('nickname', 'remark'):
            value = users.get(uid, {}).get(key)
            if value:
                aliases[value].add(uid)
    result, ids, fallbacks = [], set(), Counter()
    for row in rows:
        if row['room_name'] != TARGET or row['talker'] not in (OWNER, TARGET):
            raise ValueError('Unexpected conversation or sender')
        if row['is_sender'] not in (0, 1) or row['talker'] != (OWNER if row['is_sender'] else TARGET):
            raise ValueError('Sender identity disagrees with native direction flag')
        name, text = row['type_name'], row['msg']
        if not isinstance(text, str):
            raise ValueError('Native message body must be a string')
        ts = timestamp(row['CreateTime'])
        native_id = row['MsgSvrID']
        if native_id and native_id != '0':
            mid = 'wx:' + native_id
        else:
            # Stable across full re-exports; native sequential row IDs are not stable.
            digest = hashlib.sha256(encode([TARGET, ts, row['talker'], name, text]).encode()).hexdigest()
            fallbacks[digest] += 1
            mid = f'wx:fallback:{digest}:{fallbacks[digest]}'
        if mid in ids:
            raise ValueError('Duplicate platform message ID')
        ids.add(mid)
        quote = None
        if name == '引用回复':
            match = QUOTE.fullmatch(text)
            if not match:
                raise ValueError('Quote format does not match audited native renderer')
            text, quoted_time, author, quoted_text = match.groups()
            known = aliases.get(author, set())
            quote = {'time': quoted_time, 'author': author, 'text': quoted_text,
                     'sender_id': next(iter(known)) if len(known) == 1 else None,
                     'message_id': None, 'link_status': 'unresolved'}
        is_system = name in ('系统通知', '消息撤回57', '拍一拍')
        sender = 'SYSTEM' if is_system else row['talker']
        result.append({'id': mid, 'platform': 'weixin', 'conversation_id': TARGET,
                       'timestamp': ts, 'time': datetime.fromtimestamp(ts, TZ).isoformat(),
                       'sender_id': sender, 'sender_name': '系统消息' if is_system else ('我' if sender == OWNER else '目标联系人'),
                       'text': text, 'category': name, 'type': TYPE_MAP.get(name, 24 if name.startswith('(分享)') else 99),
                       'quote': quote, 'native_sender_id': row['talker'], 'native_is_sender': row['is_sender'],
                       'native_message_id': native_id, 'native_row_id': row['id'],
                       'media_reference': row.get('src'), 'native_extra': row.get('extra'),
                       'source_message_text': row['msg']})
    candidates = defaultdict(list)
    for msg in result:
        if msg['type'] in (0, 25):
            candidates[(msg['timestamp'], msg['sender_id'], msg['text'])].append(msg['id'])
    for msg in result:
        q = msg['quote']
        if not q or not q['sender_id']:
            continue
        try:
            qt = timestamp(q['time'])
        except ValueError:
            continue
        matching = candidates.get((qt, q['sender_id'], q['text']), [])
        if len(matching) == 1 and matching[0] != msg['id'] and qt <= msg['timestamp']:
            q.update(message_id=matching[0], link_status='exact_time_sender_text')
    return result


def run():
    latest = json.loads((ROOT / 'pywxdump-latest.json').read_text(encoding='utf-8'))
    source = Path(latest['output']).resolve()
    if not source.is_relative_to(ROOT / 'pywxdump-exports'):
        raise ValueError('Unexpected source directory')
    files = sorted(source.glob(TARGET + '_*.json'))
    rows = [r for file in files for r in json.loads(file.read_text(encoding='utf-8'))]
    report = json.loads((source / '提取报告.json').read_text(encoding='utf-8'))
    if len(rows) != report['exported_messages']:
        raise ValueError('Source count differs from native extraction report')
    users = json.loads((source / 'users.json').read_text(encoding='utf-8'))
    messages = convert(rows, users)
    output = ROOT / 'chatlab-exports' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    output.mkdir(parents=True, exist_ok=False)
    chatlab = output / '目标联系人_微信_ChatLab.jsonl'
    rag = output / '目标联系人_微信_RAG.jsonl'
    with chatlab.open('x', encoding='utf-8') as cf, rag.open('x', encoding='utf-8') as rf:
        cf.write(encode({'_type': 'header', 'chatlab': {'version': '0.0.2', 'exportedAt': int(datetime.now().timestamp()), 'generator': 'PyWxDump-to-ChatLab'},
                         'meta': {'name': '目标联系人（微信）', 'platform': 'weixin', 'type': 'private', 'ownerId': OWNER, 'sourceSessionId': SESSION}}) + '\n')
        for uid, name in ((OWNER, '我'), (TARGET, '目标联系人'), ('SYSTEM', '系统消息')):
            cf.write(encode({'_type': 'member', 'platformId': uid, 'accountName': name}) + '\n')
        for msg in messages:
            cm = {'_type': 'message', 'sender': msg['sender_id'], 'accountName': msg['sender_name'],
                  'timestamp': msg['timestamp'], 'type': msg['type'], 'content': msg['text'], 'platformMessageId': msg['id']}
            if msg['quote'] and msg['quote']['message_id']:
                cm['replyToMessageId'] = msg['quote']['message_id']
            cf.write(encode(cm) + '\n')
            rf.write(encode(msg) + '\n')
    counts = Counter(m['sender_name'] for m in messages)
    quotes = [m for m in messages if m['quote']]
    linked = sum(bool(m['quote']['message_id']) for m in quotes)
    stats = {'messages': len(messages), 'senders': dict(counts), 'types': dict(Counter(m['category'] for m in messages)),
             'quote_count': len(quotes), 'exact_quote_links': linked, 'unresolved_quote_links': len(quotes) - linked,
             'source': str(source), 'session_id': SESSION, 'chatlab_file': str(chatlab), 'rag_file': str(rag),
             'source_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
             'first': messages[0]['time'], 'last': messages[-1]['time']}
    (output / '转换报告.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'chatlab-latest.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(stats, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    run()
