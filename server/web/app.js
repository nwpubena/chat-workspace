'use strict';
let token = '';
let results = [];
const $ = id => document.getElementById(id);
const format = value => Number(value).toLocaleString('zh-CN');
const labels = {wechat: '微信', douyin: '抖音'};
function notice(message, error = false) { $('notice').hidden = false; $('notice').textContent = message; $('notice').classList.toggle('error', error); }
async function api(path, data) {
  const response = await fetch(path, {method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-Control-Token': token}, ...(data === undefined ? {} : {body: JSON.stringify(data)})});
  if (response.redirected || response.status === 401) { throw new Error('博客登录已过期，请点击右上角“博客后台”重新登录。'); }
  let value;
  try { value = await response.json(); } catch { throw new Error('服务响应异常，请检查登录状态或服务器日志。'); }
  if (!response.ok) throw new Error(value.detail || `请求失败：${response.status}`);
  return value;
}
function element(tag, className, value) { const node = document.createElement(tag); if (className) node.className = className; if (value !== undefined) node.textContent = value; return node; }
function rows(container, values, name) {
  const node = $(container); node.replaceChildren();
  if (!values.length) { node.append(element('p', 'muted', '暂无导入记录')); return; }
  values.forEach(row => { const line = element('div', 'report-row'); line.append(element('span', '', name(row)), element('strong', '', format(row.count))); node.append(line); });
}
async function overview() {
  const info = await api('api/overview');
  info.records.forEach(row => { $(`${row.platform}-count`).textContent = format(row.count); });
  $('conversation-count').textContent = format(info.conversations.length);
  const select = $('search-conversation'); const previous = select.value;
  select.replaceChildren(new Option('全部会话', ''));
  const platform = $('search-platform').value;
  [...new Set(info.conversations.filter(r => platform === 'all' || r.platform === platform).map(r => r.conversation))].forEach(name => select.add(new Option(name, name)));
  if ([...select.options].some(o => o.value === previous)) select.value = previous;
  rows('conversation-report', info.conversations, r => `${labels[r.platform]} · ${r.conversation}`);
  rows('sender-report', info.senders, r => `${labels[r.platform]} · ${r.sender}`);
  rows('day-report', info.days, r => r.day);
}
document.querySelectorAll('[data-panel]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-panel]').forEach(tab => { const selected = tab === button; tab.setAttribute('aria-selected', String(selected)); $(`${tab.dataset.panel}-panel`).hidden = !selected; });
}));
$('search-platform').addEventListener('change', () => overview().catch(error => notice(error.message, true)));
$('search-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector('button'); button.disabled = true;
  try {
    const info = await api('api/search', {query: $('query').value, platform: $('search-platform').value, conversation: $('search-conversation').value, sender: $('sender').value, since: $('since').value, until: $('until').value, limit: 30});
    results = info.messages; $('results').replaceChildren(); $('export-results').hidden = !results.length;
    $('result-summary').textContent = `找到 ${format(info.total)} 条记录，显示 ${results.length} 条${info.truncated ? '；请缩小筛选范围查看更多' : ''}`;
    if (!results.length) $('results').append(element('div', 'empty', '没有找到匹配记录，试试更短的关键词。'));
    results.forEach(message => {
      const card = element('article', 'message'); const meta = element('div', 'message-meta');
      meta.append(element('span', 'tag', labels[message.platform]), element('strong', '', message.sender), element('time', '', message.time.replace('T', ' ').slice(0, 19)));
      card.append(meta, element('p', 'message-body', message.content), element('p', 'message-id', `会话：${message.conversation} · ID：${message.platform_message_id}`));
      if (message.context.length) {
        const details = element('details'); details.append(element('summary', '', `查看附近 ${message.context.length} 条对话`));
        message.context.forEach(context => { const row = element('div', 'context'); row.append(element('span', 'message-id', `${context.sender} · ${context.time.replace('T', ' ').slice(0, 19)}`), element('p', 'message-body', context.content)); details.append(row); });
        card.append(details);
      }
      $('results').append(card);
    });
  } catch (error) { notice(error.message, true); } finally { button.disabled = false; }
});
$('export-results').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify({exported_at: new Date().toISOString(), messages: results}, null, 2)], {type: 'application/json;charset=utf-8'});
  const url = URL.createObjectURL(blob); const link = element('a'); link.href = url; link.download = `workspace-search-${Date.now()}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$('import-form').addEventListener('submit', async event => {
  event.preventDefault(); const file = $('import-file').files[0]; if (!file) return;
  if (file.size > 10 * 1024 * 1024) { notice('文件超过 10 MiB，请拆分后导入。', true); return; }
  if (!$('import-consent').checked) return;
  $('import-button').disabled = true; $('import-result').textContent = '正在读取并导入，请保持页面打开…';
  try {
    const response = await api('api/import', {platform: $('import-platform').value, conversation: $('import-conversation').value, text: await file.text()});
    $('import-result').textContent = `导入完成：新增 ${format(response.inserted)} 条，跳过重复 ${format(response.skipped)} 条。会话：${response.conversation}`;
    $('import-consent').checked = false; await overview();
  } catch (error) { $('import-result').textContent = error.message; notice(error.message, true); } finally { $('import-button').disabled = false; }
});
(async () => { try { token = (await api('api/bootstrap')).token; await overview(); } catch (error) { notice(error.message, true); } })();
