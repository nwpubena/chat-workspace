"""Descriptive counts only: no LLM, personality inference, or remote calls."""
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
from convert_pywxdump import ROOT


def run():
    meta = json.loads((ROOT / 'chatlab-latest.json').read_text(encoding='utf-8'))
    source = Path(meta['rag_file'])
    with source.open(encoding='utf-8') as f:
        messages = [json.loads(line) for line in f]
    human = [m for m in messages if m['sender_id'] != 'SYSTEM']
    months, hours, days = defaultdict(Counter), Counter(), Counter()
    for m in human:
        months[m['time'][:7]][m['sender_name']] += 1
        hours[int(m['time'][11:13])] += 1
        days[m['time'][:10]] += 1
    textual = [m for m in human if m['type'] in (0, 25)]
    counts = Counter(m['sender_name'] for m in human)
    first_date = datetime.fromisoformat(messages[0]['time']).date()
    last_date = datetime.fromisoformat(messages[-1]['time']).date()
    lines = ['# 目标联系人微信聊天基线报告', '',
             f"统计范围：{meta['first']} 至 {meta['last']}（北京时间）。", '',
             '这是本地记录的描述性统计，不是性格、感情或关系判断。手机独有记录可能不在本数据中。', '',
             f"共 {len(messages):,} 条记录，其中双方消息 {len(human):,} 条、系统事件 {len(messages)-len(human):,} 条。",
             f"普通文字与引用回复正文共 {len(textual):,} 条；引用文字不重复计入当前发言正文。", '',
             '| 发送方 | 消息数（含媒体） | 占双方消息比例 |', '| --- | ---: | ---: |']
    for sender in ('我', '目标联系人'):
        lines.append(f'| {sender} | {counts[sender]:,} | {counts[sender]/len(human):.1%} |')
    lines += ['', '## 月度分布', '', '| 月份 | 我 | 目标联系人 |', '| --- | ---: | ---: |']
    for month in sorted(months):
        lines.append(f"| {month} | {months[month]['我']:,} | {months[month]['目标联系人']:,} |")
    lines += ['', '首尾月份不完整，不宜直接把月总数变化解释为关系升温或降温。', '',
              '## 聊天时间分布', '',
              f'覆盖 {(last_date-first_date).days+1} 个自然日，其中 {len(days)} 天有双方消息。',
              '消息最多的三个小时段（含文字和媒体消息）：', '']
    for hour, count in hours.most_common(3):
        lines.append(f'- {hour:02d}:00–{hour:02d}:59：{count:,} 条，占双方消息 {count/len(human):.1%}。')
    lines += ['', '消息发送时间只反映记录中的活动，不能据此断言对方作息或空闲时间。', '',
              '## 数据整理与检索', '',
              f"{meta['quote_count']:,} 条引用回复已分离正文，其中 {meta['exact_quote_links']:,} 条建立了精确引用关联。",
              f"剩余 {meta['unresolved_quote_links']:,} 条仍保留引用时间、作者和内容，但因不能唯一匹配原消息，不建立猜测链接。",
              '完整关键词检索已可用；语义检索覆盖率以 ChatLab 当前索引进度为准，未完成前不能用“未搜到”证明“没说过”。', '',
              '## 当前不作出的推断', '',
              '没有把连续消息间隔当作真实回复耗时，也没有把消息数量差异解释为主动程度或投入程度。',
              '图片和视频尚未做内容识别，语音尚未转写，不能视作已分析其中的语义。',
              '后续生成建议或总结，应附消息时间与编号，并区分原话、推断和建议；不会自动代发消息。', '',
              f"数据来源：`{source.relative_to(ROOT.parent)}`。",
              f"原生提取来源：`{Path(meta['source']).relative_to(ROOT.parent)}`。", '']
    output = ROOT / '目标联系人_微信_聊天基线报告.md'
    output.write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'report': str(output), 'human_messages':len(human),'system_events':len(messages)-len(human),
                      'text_and_reply_messages':len(textual), 'active_days':len(days)}, ensure_ascii=False))


if __name__ == '__main__':
    run()
