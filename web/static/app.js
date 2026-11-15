'use strict';
const S = { me: null, csrf: '', gid: localStorage.getItem('gid') || '', meta: null, dirty: false };
const $app = document.getElementById('app');

/* ---------- утилиты ---------- */
const h = (tag, attrs, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'html') e.innerHTML = v;
    else if (v !== false && v != null) e.setAttribute(k, v === true ? '' : v);
  }
  for (const x of kids.flat()) if (x != null && x !== false) e.append(x.nodeType ? x : document.createTextNode(x));
  return e;
};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const ts = t => t ? new Date(t * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : '—';

function toast(msg, err) {
  const t = h('div', { class: 'toast' + (err ? ' err' : '') }, msg);
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), err ? 6000 : 3200);
}

async function api(path, opt = {}) {
  const r = await fetch('/api' + path, {
    method: opt.method || 'GET',
    headers: { 'Content-Type': 'application/json', 'X-CSRF': S.csrf },
    body: opt.body !== undefined ? JSON.stringify(opt.body) : undefined,
  });
  if (r.status === 401) { showLogin(); throw new Error('Нужен вход'); }
  if (!r.ok) {
    let m = r.statusText;
    try { const j = await r.json(); m = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch { }
    throw new Error(m);
  }
  return r.json();
}
const G = p => `/g/${S.gid}${p}`;

/* ---------- построитель полей по схеме ---------- */
function chanOptions(val) {
  const sel = h('select');
  sel.append(h('option', { value: '' }, '— не выбран —'));
  const groups = new Map();
  for (const c of S.meta.channels) {
    if (c.type === 'voice') continue;
    const g = c.type === 'category' ? 'Категории' : (c.parent || 'Без категории');
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(c);
  }
  for (const [g, list] of groups) {
    const og = h('optgroup', { label: g });
    for (const c of list) og.append(h('option', { value: c.id }, (c.type === 'category' ? '▸ ' : '# ') + c.name));
    sel.append(og);
  }
  if (val && ![...sel.options].some(o => o.value === val)) sel.append(h('option', { value: val }, `(удалённый канал ${val})`));
  sel.value = val || '';
  return sel;
}
const roleName = id => S.meta.roles.find(r => r.id === id)?.name || `удалена (${id})`;
const roleColor = id => S.meta.roles.find(r => r.id === id)?.color || '';

function control(f, val) {
  const t = f.type;
  const fire = el => el.dispatchEvent(new Event('input', { bubbles: true }));
  if (t === 'bool') {
    const i = h('input', { type: 'checkbox' }); i.checked = !!val;
    return { el: h('label', { class: 'sw' }, i, h('span', { class: 't' }), h('span', {}, f.label)), get: () => i.checked, inline: true };
  }
  if (t === 'textarea') { const i = h('textarea'); i.value = val ?? ''; return { el: i, get: () => i.value }; }
  if (t === 'number') { const i = h('input', { type: 'number', step: 'any' }); i.value = val ?? 0; return { el: i, get: () => i.value === '' ? 0 : Number(i.value) }; }
  if (t === 'color') {
    const c = h('input', { type: 'color' }), tx = h('input', { type: 'text', placeholder: 'по умолчанию' });
    c.value = /^#[0-9a-f]{6}$/i.test(val) ? val : '#b91c1c'; tx.value = val || '';
    c.addEventListener('input', e => { tx.value = c.value; });
    tx.addEventListener('input', () => { if (/^#[0-9a-f]{6}$/i.test(tx.value)) c.value = tx.value; });
    return { el: h('div', { class: 'color' }, c, tx), get: () => tx.value.trim() };
  }
  if (t === 'select') {
    const s = h('select', {}, f.options.map(o => h('option', { value: o[0] }, o[1]))); s.value = val ?? f.default;
    return { el: s, get: () => s.value };
  }
  if (t === 'channel') { const s = chanOptions(val); return { el: s, get: () => s.value || null }; }
  if (t === 'role') {
    const s = h('select', {}, h('option', { value: '' }, '— не выбрана —'), S.meta.roles.map(r => h('option', { value: r.id }, r.name)));
    s.value = val || ''; return { el: s, get: () => s.value || null };
  }
  if (t === 'roles') {
    let cur = [...(val || [])];
    const chips = h('div', { class: 'chips' });
    const add = h('select', {}, h('option', { value: '' }, '+ добавить роль…'), S.meta.roles.map(r => h('option', { value: r.id }, r.name)));
    const draw = () => {
      chips.replaceChildren(...cur.map(id => h('span', { class: 'chip' }, h('i', { style: `background:${roleColor(id) || 'var(--dim)'}` }), roleName(id),
        h('button', { type: 'button', 'aria-label': 'Убрать', onclick: () => { cur = cur.filter(x => x !== id); draw(); fire(chips); } }, '×'))));
    };
    add.addEventListener('change', e => { e.stopPropagation(); if (add.value && !cur.includes(add.value)) { cur.push(add.value); draw(); fire(chips); } add.value = ''; });
    draw();
    return { el: h('div', {}, chips, add), get: () => cur };
  }
  if (t === 'action') {
    const TYPES = [['', '— ничего не делает —'], ['form', 'Открыть форму'], ['ticket', 'Открыть тикет'], ['cab', 'Личный кабинет'],
      ['file', 'Моё личное дело'], ['roster', 'Показать составы'], ['role', 'Выдать / снять роль'], ['reply', 'Ответить текстом'], ['url', 'Открыть ссылку']];
    const [k0, ...r0] = String(val || '').split(':'); const arg0 = r0.join(':');
    const isUrl = /^https?:\/\//.test(val || '');
    const type = h('select', {}, TYPES.map(([v, l]) => h('option', { value: v }, l)));
    const targets = { form: S.meta.forms || [], ticket: S.meta.tickets || [], role: S.meta.roles.map(r => ({ value: r.id, label: r.name })) };
    const tsel = h('select', {}), url = h('input', { type: 'text', placeholder: 'https://…' });
    const hint = h('small', { class: 'hint' });
    const fill = (cur) => {
      const kind = type.value, list = targets[kind];
      tsel.classList.toggle('hidden', !list); url.classList.toggle('hidden', kind !== 'url');
      hint.textContent = kind === 'reply' ? 'Текст ответа впишите в поле ниже.' : kind === 'form' && !(list || []).length ? 'Сначала создайте форму в разделе «Формы заявок».' : '';
      if (list) {
        tsel.replaceChildren(...list.map(o => h('option', { value: o.value }, o.label)));
        if (cur && !list.some(o => o.value === cur)) tsel.append(h('option', { value: cur }, `(удалено) ${cur}`));
        tsel.value = cur || (list[0] ? list[0].value : '');
      }
    };
    if (isUrl) { type.value = 'url'; url.value = val; }
    else if (TYPES.some(([v]) => v === k0)) type.value = k0; else type.value = '';
    fill(arg0);
    type.addEventListener('change', () => fill(''));
    const get = () => { const k = type.value;
      if (k === 'url') return url.value.trim();
      if (k === 'reply') return 'reply';
      if (targets[k]) return tsel.value ? `${k}:${tsel.value}` : '';
      if (k === 'cab') return 'cab:home'; if (k === 'file') return 'file:me'; if (k === 'roster') return 'roster:show';
      return ''; };
    return { el: h('div', { style: 'display:grid;gap:6px' }, type, tsel, url, hint), get };
  }
  if (t === 'list') return listControl(f, val || []);
  const i = h('input', { type: 'text' }); i.value = val ?? ''; return { el: i, get: () => i.value };
}

function listControl(f, rows) {
  const box = h('div', { class: 'rows' });
  const addBtn = h('button', { class: 'btn', type: 'button' }, `+ Добавить`);
  const items = [];
  const fire = () => box.dispatchEvent(new Event('input', { bubbles: true }));
  const refresh = () => { addBtn.disabled = items.length >= (f.max || 25); box.replaceChildren(...items.map(i => i.el)); };
  const mk = (data) => {
    const form = buildForm(f.item, data, true);
    const it = { form };
    const mv = d => () => { const i = items.indexOf(it), j = i + d; if (j < 0 || j >= items.length) return; [items[i], items[j]] = [items[j], items[i]]; refresh(); fire(); };
    it.el = h('div', { class: 'rowi' }, h('div', { class: 'grid' }, form.el),
      h('div', { class: 'tools' },
        h('button', { class: 'btn sm', type: 'button', title: 'Выше', onclick: mv(-1) }, '↑'),
        h('button', { class: 'btn sm', type: 'button', title: 'Ниже', onclick: mv(1) }, '↓'),
        h('button', { class: 'btn sm bad', type: 'button', title: 'Удалить', onclick: () => { items.splice(items.indexOf(it), 1); refresh(); fire(); } }, '✕')));
    return it;
  };
  rows.forEach(r => items.push(mk(r)));
  addBtn.onclick = () => { items.push(mk(Object.fromEntries(f.item.map(x => [x.key, x.default])))); refresh(); fire(); };
  refresh();
  return { el: h('div', {}, box, h('div', { style: 'margin-top:10px' }, addBtn)), get: () => items.map(i => i.form.collect()) };
}

function buildForm(fields, values, compact) {
  const ctl = {};
  const els = fields.map(f => {
    const c = control(f, values?.[f.key] ?? f.default);
    ctl[f.key] = c;
    if (c.inline) return h('div', { class: 'field' }, c.el);
    return h('div', { class: 'field' }, h('label', { class: 'l' }, f.label), c.el);
  });
  return { el: compact ? els : h('div', { class: 'fields' }, els), ctl, collect: () => Object.fromEntries(fields.map(f => [f.key, ctl[f.key].get()])) };
}

/* ---------- панель сохранения ---------- */
function saveBar(onSave, onReset) {
  const bar = h('div', { class: 'savebar hidden' }, h('span', {}, 'Есть несохранённые изменения'),
    h('button', { class: 'btn', onclick: onReset }, 'Отменить'),
    h('button', { class: 'btn pri', onclick: async e => { e.target.disabled = true; try { await onSave(); bar.classList.add('hidden'); S.dirty = false; } catch (er) { toast(er.message, true); } e.target.disabled = false; } }, 'Сохранить'));
  return { bar, mark() { bar.classList.remove('hidden'); S.dirty = true; }, hide() { bar.classList.add('hidden'); S.dirty = false; } };
}

/* ---------- каркас ---------- */
function showLogin(msg) {
  $app.replaceChildren(h('div', { class: 'login' }, h('div', {},
    h('h1', {}, 'Панель управления'),
    h('p', {}, msg || 'Войдите через Discord, чтобы настроить бота на своём сервере.'),
    h('a', { class: 'btn pri', href: '/login', style: 'text-decoration:none;display:inline-block' }, 'Войти через Discord'))));
}

const SETTINGS_ICON = {};
function nav() {
  const r = location.hash || '#/overview';
  const a = (href, icon, text) => h('a', { href, class: r === href ? 'on' : '' }, h('span', {}, icon), text);
  const sch = S.meta.settings_schema, ik = S.meta.item_schemas;
  return h('nav', { class: 'nav' },
    a('#/overview', '🏠', 'Обзор'),
    h('h4', {}, 'Настройки'), sch.map(g => a(`#/s/${g.group}`, g.icon, g.title)),
    h('h4', {}, 'Конструктор'), Object.entries(ik).map(([k, v]) => a(`#/i/${k}`, v.icon, v.title)), a('#/news', '📰', 'Отправить новость'),
    h('h4', {}, 'Данные'), Object.entries(TBL).map(([k, v]) => a(`#/d/${k}`, v.icon, v.title)),
    h('h4', {}, 'Сервис'), a('#/setup', '🪄', 'Автонастройка каналов'), a('#/backup', '💾', 'Экспорт и импорт'));
}

async function shell() {
  const gsel = h('select', { 'aria-label': 'Сервер', onchange: e => { S.gid = e.target.value; localStorage.setItem('gid', S.gid); boot(true); } },
    S.me.guilds.map(g => h('option', { value: g.id }, g.name)));
  gsel.value = S.gid;
  const side = h('aside', { class: 'side' },
    h('div', { class: 'brand' }, h('i'), h('div', {}, h('b', {}, 'Панель управления'), h('small', {}, S.meta.guild.name))),
    gsel, nav(),
    h('div', { class: 'me' }, S.me.user.avatar && h('img', { src: S.me.user.avatar, alt: '' }), h('span', {}, S.me.user.name), h('a', { href: '/logout' }, 'Выйти')));
  const main = h('main', { id: 'main' });
  $app.replaceChildren(h('div', { class: 'shell' }, side, main));
  return main;
}

async function boot(keep) {
  try {
    S.me = await api('/me'); S.csrf = S.me.csrf;
  } catch { return; }
  if (!S.me.guilds.length) {
    return $app.replaceChildren(h('div', { class: 'login' }, h('div', {},
      h('h1', {}, 'Нет доступных серверов'),
      h('p', {}, 'Бот не найден ни на одном сервере, где у вас есть права администратора. Добавьте бота и обновите страницу.'),
      h('a', { class: 'btn pri', href: S.me.invite, style: 'text-decoration:none;display:inline-block' }, 'Добавить бота на сервер'))));
  }
  if (!S.me.guilds.some(g => g.id === S.gid)) S.gid = S.me.guilds[0].id;
  localStorage.setItem('gid', S.gid);
  S.meta = await api(G('/meta'));
  route();
}

async function route() {
  S.dirty = false;
  const main = await shell();
  const [, kind, a, b] = (location.hash || '#/overview').split('/');
  try {
    if (kind === 's') await viewSettings(main, a);
    else if (kind === 'i') await viewItems(main, a, b);
    else if (kind === 'd') await viewData(main, a);
    else if (kind === 'news') await viewNews(main);
    else if (kind === 'setup') await viewSetup(main);
    else if (kind === 'backup') viewBackup(main);
    else await viewOverview(main);
  } catch (e) { main.append(h('p', { class: 'sub' }, 'Ошибка: ' + e.message)); }
}
window.addEventListener('hashchange', route);

/* ---------- обзор ---------- */
async function viewOverview(main) {
  const o = await api(G('/overview'));
  const st = o.stats, L = { members: 'Участников', pending: 'Заявок на рассмотрении', profiles: 'Профилей', roster: 'В составах', tickets: 'Открытых тикетов', warns: 'Активных предупреждений' };
  main.append(h('h1', { class: 'title' }, S.meta.guild.name), h('p', { class: 'sub' }, `Бот: ${S.me.bot.name} · отклик ${S.me.bot.latency} мс`),
    h('div', { class: 'stats' }, Object.entries(L).map(([k, t]) => h('div', {}, h('b', {}, st[k]), h('span', {}, t)))));
  main.append(h('h3', { class: 'sec' }, 'Модули'),
    h('p', { class: 'sub', style: 'margin-bottom:12px' }, 'Что включено на сервере. Выключенный модуль перестаёт отвечать на команды и кнопки.'),
    h('div', { class: 'mods' }, o.status.modules.map(m => h('a', { href: '#/s/modules', class: 'mod ' + (m.on ? 'on' : 'off') }, h('span', {}, m.icon), m.name, h('em', {}, m.on ? 'работает' : 'выключен')))));
  main.append(h('h3', { class: 'sec' }, 'Проверка настройки'),
    h('div', { class: 'checks' }, o.status.checks.map(c => {
      const tag = c.route ? 'a' : 'div';
      return h(tag, { class: 'chk ' + (c.ok === true ? 'ok' : c.ok === false ? 'bad' : 'opt'), href: c.route || undefined },
        h('b', {}, c.ok === true ? '✓' : c.ok === false ? '✕' : '–'), c.text);
    })));
  if (o.status.checks.some(c => c.ok === false)) main.append(h('p', { class: 'sub', style: 'margin-top:18px' }, 'Не хотите создавать каналы вручную? ', h('a', { href: '#/setup' }, 'Бот создаст их сам'), '.'));
  main.append(h('p', { class: 'sub', style: 'margin-top:24px' }, 'В Discord: /status — то же самое, /setting — быстрая настройка, /news — новость, /help — все команды.'));
}

/* ---------- настройки ---------- */
async function viewSettings(main, gkey) {
  const grp = S.meta.settings_schema.find(g => g.group === gkey);
  if (!grp) return main.append('Раздел не найден');
  const vals = await api(G('/settings'));
  let form = buildForm(grp.fields, vals);
  const holder = h('div', {}, form.el);
  const sb = saveBar(async () => {
    await api(G('/settings'), { method: 'PUT', body: form.collect() });
    toast('Сохранено');
  }, () => { form = buildForm(grp.fields, vals); holder.replaceChildren(form.el); sb.hide(); });
  const onChange = () => sb.mark();
  holder.addEventListener('input', onChange); holder.addEventListener('change', onChange);
  main.append(h('h1', { class: 'title' }, `${grp.icon} ${grp.title}`), h('p', { class: 'sub' }, 'Изменения применяются сразу после сохранения, перезапуск бота не нужен.'), holder, sb.bar);
}

/* ---------- конструктор: формы, панели, должности, тикеты ---------- */
function md(s) {
  return esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*(?!\s)(.+?)\*/g, '$1<i>$2</i>').replace(/`([^`]+)`/g, '<code>$1</code>');
}
function embedPreview(v, brand) {
  const color = v.color || brand || '#b91c1c';
  const e = h('div', { class: 'emb', style: `border-left-color:${esc(color)}` });
  if (v.title) e.append(h('div', { class: 'et' }, v.title));
  if (v.description) e.append(h('div', { class: 'ed', html: md(v.description) }));
  if (v.image) e.append(h('img', { class: 'im', src: v.image, alt: '', onerror: ev => ev.target.remove() }));
  if (v.thumbnail) e.append(h('img', { class: 'th', src: v.thumbnail, alt: '', onerror: ev => ev.target.remove() }));
  if (v.footer || brand_footer) e.append(h('div', { class: 'ef' }, v.footer || brand_footer));
  const btns = (v.buttons || []).map(b => {
    const url = /^https?:/.test(b.action || '');
    return h('span', { class: 'dbtn ' + (url ? 'link' : b.style || 'secondary') }, (b.emoji ? b.emoji + ' ' : '') + (b.label || ''));
  });
  return h('div', { class: 'prev' }, h('div', { class: 'who' }, h('i'), 'RP Bot', h('em', {}, '✔ БОТ')), e, btns.length > 0 && h('div', { class: 'btns' }, btns));
}
let brand_footer = '';

async function viewItems(main, kind, key) {
  const sch = S.meta.item_schemas[kind];
  if (!sch) return main.append('Раздел не найден');
  const [items, st] = await Promise.all([api(G(`/items/${kind}`)), api(G('/settings'))]);
  brand_footer = st.footer || '';
  const keys = Object.keys(items);
  if (!key && keys.length) { location.replace(`#/i/${kind}/${keys[0]}`); return; }
  const label = (k, v) => v.title || v.name || k;

  const list = h('div', { class: 'items' }, keys.map(k => h('a', { href: `#/i/${kind}/${k}`, class: k === key ? 'on' : '' }, label(k, items[k]), h('small', {}, k))));
  const nk = h('input', { type: 'text', placeholder: sch.key_hint || 'ключ', 'aria-label': 'Ключ нового элемента' });
  const addNew = async () => {
    const k = nk.value.trim().toLowerCase();
    try { await api(G(`/items/${kind}/${encodeURIComponent(k)}`), { method: 'PUT', body: {} }); S.meta = await api(G('/meta')); S.dirty = false; location.hash = `#/i/${kind}/${k}`; }
    catch (e) { toast(e.message, true); }
  };
  nk.addEventListener('keydown', e => e.key === 'Enter' && addNew());
  const side = h('div', {}, list, h('div', { class: 'newitem' }, nk, h('button', { class: 'btn pri', onclick: addNew, title: `Добавить ${sch.single}` }, '+')));

  const right = h('div', {});
  main.append(h('h1', { class: 'title' }, `${sch.icon} ${sch.title}`), h('p', { class: 'sub' }, 'Добавляйте и удаляйте элементы — они сразу становятся доступны в боте.'), h('div', { class: 'split' }, side, right));
  if (!key || !items[key]) { right.append(h('div', { class: 'empty' }, `Пока нет ни одной записи. Введите ключ слева и нажмите «+», чтобы добавить ${sch.single}.`)); return; }

  let data = items[key], form = buildForm(sch.fields, data);
  const grid = h('div', { class: 'edit-grid' + (kind === 'panel' ? ' has-prev' : '') });
  const formBox = h('div', {}, form.el);
  let prev = null;
  const redraw = () => { if (kind !== 'panel') return; const n = embedPreview(form.collect(), st.brand_color); prev?.replaceWith(n); prev = n; };
  const sb = saveBar(async () => { data = await api(G(`/items/${kind}/${key}`), { method: 'PUT', body: form.collect() }); S.meta = await api(G('/meta')); toast(data._publish_error ? 'Сохранено, но панель не обновилась: ' + data._publish_error : (kind === 'panel' && data._msg ? 'Сохранено и обновлено в Discord' : 'Сохранено'), !!data._publish_error); },
    () => { form = buildForm(sch.fields, data); formBox.replaceChildren(form.el); redraw(); sb.hide(); });
  const onChange = () => { sb.mark(); redraw(); };
  formBox.addEventListener('input', onChange); formBox.addEventListener('change', onChange);

  const bar = h('div', { class: 'bar' }, h('code', {}, key));
  if (kind === 'panel') {
    bar.append(h('button', { class: 'btn pri', onclick: async e => {
      e.target.disabled = true;
      try { if (S.dirty) { await api(G(`/items/${kind}/${key}`), { method: 'PUT', body: form.collect() }); sb.hide(); }
        const r = await api(G(`/panels/${key}/publish`), { method: 'POST' }); toast('Опубликовано в Discord'); data._msg = 1; }
      catch (er) { toast(er.message, true); }
      e.target.disabled = false;
    } }, data._msg ? 'Обновить в Discord' : 'Опубликовать'));
  }
  bar.append(h('button', { class: 'btn bad', onclick: async () => {
    if (!confirm(`Удалить «${label(key, data)}»?`)) return;
    const delMsg = kind === 'panel' && data._msg && confirm('Удалить и опубликованное сообщение в Discord?');
    try { await api(G(`/items/${kind}/${key}?delete_message=${delMsg}`), { method: 'DELETE' }); S.meta = await api(G('/meta')); S.dirty = false; location.hash = `#/i/${kind}`; } catch (er) { toast(er.message, true); }
  } }, 'Удалить'));
  right.append(bar, grid);
  grid.append(formBox);
  if (kind === 'panel') { prev = embedPreview(data, st.brand_color); grid.append(prev); }
  right.append(sb.bar);
}

/* ---------- новости ---------- */
async function viewNews(main) {
  const [st, hist] = await Promise.all([api(G('/settings')), api(G('/news'))]);
  brand_footer = st.footer || '';
  const fields = [
    { key: 'channel', label: 'Канал', type: 'channel', default: st.news_channel || null },
    { key: 'ping_roles', label: 'Упомянуть роли', type: 'roles', default: [] },
    { key: 'title', label: 'Заголовок', type: 'text', default: '' },
    { key: 'description', label: 'Текст (поддерживается **жирный**, *курсив*, списки через «•»)', type: 'textarea', default: '' },
    { key: 'color', label: 'Цвет (пусто = фирменный)', type: 'color', default: '' },
    { key: 'image', label: 'Картинка (URL)', type: 'text', default: '' },
    { key: 'thumbnail', label: 'Миниатюра (URL)', type: 'text', default: '' },
    { key: 'footer', label: 'Подпись (пусто = общая)', type: 'text', default: '' },
    { key: 'buttons', label: 'Кнопки-ссылки (до 25)', type: 'list', max: 25, default: [], item: [
      { key: 'label', label: 'Текст', type: 'text', default: '' }, { key: 'url', label: 'Ссылка', type: 'text', default: 'https://' }, { key: 'emoji', label: 'Эмодзи', type: 'text', default: '' }] },
  ];
  let form = buildForm(fields, {});
  const formBox = h('div', {}, form.el);
  let prev = null;
  const draw = () => {
    const v = form.collect();
    const n = embedPreview({ ...v, buttons: v.buttons.map(b => ({ ...b, action: b.url || 'https://x', style: 'secondary' })) }, st.brand_color);
    prev ? prev.replaceWith(n) : null; prev = n;
  };
  prev = embedPreview({ title: '', description: '', buttons: [] }, st.brand_color);
  formBox.addEventListener('input', draw); formBox.addEventListener('change', draw);
  const histBox = h('div', {});
  const drawHist = rows => histBox.replaceChildren(rows.length ? h('div', { class: 'tw' }, h('table', {}, h('thead', {}, h('tr', {}, ['Дата', 'Заголовок', 'Автор', ''].map(t => h('th', {}, t)))),
    h('tbody', {}, rows.map(r => h('tr', {}, h('td', {}, ts(r.ts)), h('td', {}, h('a', { href: r.jump, target: '_blank', rel: 'noopener' }, r.title || '(без заголовка)')), h('td', {}, who(r, 'author')),
      h('td', {}, h('button', { class: 'btn sm bad', onclick: async () => {
        if (!confirm('Удалить новость и сообщение в Discord?')) return;
        try { await api(G(`/news/${r.id}`), { method: 'DELETE' }); drawHist(await api(G('/news'))); toast('Удалено'); } catch (e) { toast(e.message, true); }
      } }, 'Удалить'))))))) : h('div', { class: 'empty' }, 'Новостей пока нет.'));
  drawHist(hist);
  const send = h('button', { class: 'btn pri', onclick: async e => {
    e.target.disabled = true;
    try {
      const r = await api(G('/news'), { method: 'POST', body: form.collect() });
      toast('Новость опубликована'); form = buildForm(fields, {}); formBox.replaceChildren(form.el); draw(); drawHist(await api(G('/news')));
      window.open(r.url, '_blank', 'noopener');
    } catch (er) { toast(er.message, true); }
    e.target.disabled = false;
  } }, 'Опубликовать');
  main.append(h('h1', { class: 'title' }, '📰 Отправить новость'),
    h('p', { class: 'sub' }, 'Сообщение придёт от имени бота. Канал по умолчанию и роли, которым разрешён /news, — в Настройки → Новости.'),
    h('div', { class: 'edit-grid has-prev' }, h('div', {}, formBox, h('div', { style: 'margin-top:18px' }, send)), prev),
    h('h3', { class: 'sec' }, 'История'), histBox);
}

/* ---------- данные ---------- */
const who = (r, k = 'user') => h('span', {}, r[k + '_name'] || r[k + '_id'] || '—', r[k + '_name'] && h('span', { class: 'mut' }, r[k + '_id']));
const tag = s => h('span', { class: 'tag ' + s }, { sent: 'отправлено', pending: 'на рассмотрении', approved: 'одобрена', denied: 'отклонена', open: 'открыт', closed: 'закрыт', active: 'активен', income: 'доход', expense: 'расход' }[s] || s);
const ACT = { hire: 'приём', promote: 'повышение', demote: 'понижение', transfer: 'перевод', fire: 'увольнение' };
const dur = s => !s ? '' : s % 86400 === 0 ? `${s / 86400} д.` : s % 3600 === 0 ? `${s / 3600} ч.` : `${Math.round(s / 60)} мин.`;
const TBL = {
  applications: { icon: '📨', title: 'Заявки', filter: [['', 'Все'], ['sent', 'Отправленные (аудит)'], ['pending', 'На рассмотрении'], ['approved', 'Одобренные'], ['denied', 'Отклонённые']], del: 'Удалить',
    cols: [['№', r => '#' + r.id], ['Заявитель', r => who(r)], ['Форма', r => r.form_key],
    ['Содержимое', r => h('div', {}, Object.entries(r.data || {}).map(([k, v]) => h('div', {}, h('span', { class: 'mut', style: 'display:inline' }, k + ': '), String(v).slice(0, 160))))],
    ['Статус', r => h('div', {}, tag(r.status), r.reviewer_id && h('span', { class: 'mut' }, 'рассмотрел: ' + (r.reviewer_name || r.reviewer_id)), r.reason && h('span', { class: 'mut' }, r.reason))],
    ['Дата', r => ts(r.created_at)]] },
  profiles: { icon: '👤', title: 'Профили', id: 'user_id', search: true, edit: true, del: 'Удалить',
    cols: [['Участник', r => who(r)], ['ФИО', r => r.fio], ['Статик', r => r.static], ['Обновлён', r => ts(r.updated_at)]] },
  roster: { icon: '🏛️', title: 'Составы', id: 'user_id', del: 'Исключить', cols: [['Сотрудник', r => who(r)], ['ФИО', r => r.fio || '—'], ['Должность', r => r.position_key], ['С', r => ts(r.since)]] },
  hr_log: { icon: '🗂️', title: 'Кадровый журнал', del: 'Удалить', cols: [['Дата', r => ts(r.ts)], ['Сотрудник', r => who(r)], ['Действие', r => ACT[r.action] || r.action], ['Было → Стало', r => `${r.from_pos || '—'} → ${r.to_pos || '—'}`], ['Издал', r => who(r, 'by')], ['Причина', r => r.reason || '']] },
  docs: { icon: '📄', title: 'Документы', del: 'Аннулировать', cols: [['№', r => '#' + r.id], ['Кому', r => who(r)], ['Вид', r => r.kind], ['Заголовок', r => r.title], ['Выдал', r => who(r, 'author')], ['Дата', r => ts(r.ts)]] },
  budget: { icon: '💰', title: 'Бюджет', del: 'Удалить', cols: [['Дата', r => ts(r.ts)], ['Тип', r => tag(r.kind)], ['Сумма', r => r.amount], ['Статья', r => r.category], ['Комментарий', r => r.comment || ''], ['Автор', r => who(r, 'author')]] },
  cases: { icon: '🛡️', title: 'Дела модерации', del: 'Снять', cols: [['№', r => '#' + r.id], ['Участник', r => who(r)], ['Тип', r => r.type + (r.active ? '' : ' (снято)')], ['Срок', r => dur(r.duration)], ['Модератор', r => who(r, 'mod')], ['Причина', r => r.reason || ''], ['Дата', r => ts(r.ts)]] },
  tickets: { icon: '🎫', title: 'Тикеты', del: 'Удалить запись', cols: [['№', r => '#' + r.id], ['Автор', r => who(r)], ['Категория', r => r.cat_key], ['Статус', r => tag(r.status)], ['Открыт', r => ts(r.created_at)], ['Закрыт', r => ts(r.closed_at)]] },
};

async function viewData(main, table) {
  const T = TBL[table];
  if (!T) return main.append('Раздел не найден');
  main.append(h('h1', { class: 'title' }, `${T.icon} ${T.title}`));
  const wrap = h('div', {}), tools = h('div', { class: 'tools-row' });
  let status = '', q = '';
  const load = async () => {
    const rows = await api(G(`/data/${table}${status ? '?status=' + status : ''}`));
    const f = q ? rows.filter(r => JSON.stringify(r).toLowerCase().includes(q)) : rows;
    if (!f.length) return wrap.replaceChildren(h('div', { class: 'tw' }, h('div', { class: 'empty' }, 'Записей нет.')));
    const idk = T.id || 'id';
    const head = h('tr', {}, T.cols.map(c => h('th', {}, c[0])), h('th'));
    const body = f.map(r => h('tr', {}, T.cols.map(c => h('td', {}, c[1](r))), h('td', { style: 'white-space:nowrap' },
      T.edit && h('button', { class: 'btn sm', onclick: async () => {
        const fio = prompt('ФИО', r.fio); if (fio === null) return;
        const st = prompt('Статик', r.static); if (st === null) return;
        try { await api(G(`/data/profiles/${r.user_id}`), { method: 'PUT', body: { fio, static: st } }); toast('Сохранено'); load(); } catch (e) { toast(e.message, true); }
      } }, 'Изменить'), ' ',
      h('button', { class: 'btn sm bad', onclick: async () => {
        if (!confirm(`${T.del}: запись «${T.cols[0][1](r).textContent || r[idk]}»?`)) return;
        try { await api(G(`/data/${table}/${r[idk]}`), { method: 'DELETE' }); toast('Готово'); load(); } catch (e) { toast(e.message, true); }
      } }, T.del))));
    wrap.replaceChildren(h('div', { class: 'tw' }, h('table', {}, h('thead', {}, head), h('tbody', {}, body))));
  };
  if (T.filter) { const s = h('select', { onchange: e => { status = e.target.value; load(); } }, T.filter.map(f => h('option', { value: f[0] }, f[1]))); tools.append(s); }
  if (T.search) tools.append(h('input', { type: 'text', placeholder: 'Поиск…', oninput: e => { q = e.target.value.toLowerCase(); load(); } }));
  main.append(tools, wrap);
  await load();
}

/* ---------- автонастройка каналов ---------- */
async function viewSetup(main) {
  const info = await api(G('/setup'));
  const checks = {}, box = h('div', { class: 'fields' });
  const pub = h('input', { type: 'checkbox' }), over = h('input', { type: 'checkbox' }); pub.checked = true;
  info.sections.forEach(sec => {
    const cb = h('input', { type: 'checkbox' }); cb.checked = true; checks[sec.key] = cb;
    box.append(h('div', { class: 'field' },
      h('label', { class: 'sw' }, cb, h('span', { class: 't' }), h('span', {}, `${sec.icon} ${sec.title}`)),
      h('div', { class: 'hint' }, sec.categories.map(c => h('div', {}, h('b', {}, c.name + (c.private ? ' (закрытая)' : '') + ': '),
        c.channels.length ? c.channels.map(x => x.name).join(', ') : 'для каналов тикетов')))));
  });
  const out = h('div', { class: 'fields', style: 'margin-top:24px' });
  const created = h('div', {});
  const drawCreated = c => created.replaceChildren(c.length ? h('div', {},
    h('h3', { class: 'sec' }, 'Создано ботом'), h('p', { class: 'sub', style: 'margin-bottom:10px' }, c.map(x => x.name).join(', ')),
    h('button', { class: 'btn bad', onclick: async () => {
      if (!confirm('Удалить все каналы и категории, созданные ботом? Это нельзя отменить.')) return;
      try { const r = await api(G('/setup/undo'), { method: 'POST' }); toast(`Удалено: ${r.deleted}`); S.meta = await api(G('/meta')); drawCreated((await api(G('/setup'))).created); out.replaceChildren(); }
      catch (e) { toast(e.message, true); }
    } }, 'Удалить всё созданное ботом')) : '');
  drawCreated(info.created);
  const go = h('button', { class: 'btn pri', onclick: async e => {
    e.target.disabled = true; e.target.textContent = 'Создаю… это может занять до минуты';
    try {
      const r = await api(G('/setup'), { method: 'POST', body: { sections: Object.keys(checks).filter(k => checks[k].checked), publish: pub.checked, overwrite: over.checked } });
      S.meta = await api(G('/meta'));
      out.replaceChildren(h('div', { class: 'field' }, h('h3', { class: 'sec', style: 'margin-top:0' }, 'Готово'), h('pre', { style: 'white-space:pre-wrap;font:inherit;margin:0' }, r.text.replace(/\*\*/g, ''))));
      drawCreated((await api(G('/setup'))).created);
    } catch (er) { toast(er.message, true); }
    e.target.disabled = false; e.target.textContent = 'Создать каналы';
  } }, 'Создать каналы');
  main.append(h('h1', { class: 'title' }, '🪄 Автонастройка каналов'),
    h('p', { class: 'sub' }, 'Бот создаст категории и каналы, выберет их в настройках, формах и панелях и опубликует панели. Уже выбранные вами каналы он не заменит. Не понравится — удалите созданное одной кнопкой и создайте всё сами: каналы выбираются в разделах настроек.'),
    box,
    h('div', { class: 'fields', style: 'margin-top:18px' },
      h('label', { class: 'sw' }, pub, h('span', { class: 't' }), h('span', {}, 'Сразу опубликовать панели с кнопками')),
      h('label', { class: 'sw' }, over, h('span', { class: 't' }), h('span', {}, 'Заменить уже выбранные каналы новыми'))),
    h('div', { style: 'margin-top:20px' }, go), out, created);
}

/* ---------- экспорт / импорт ---------- */
function viewBackup(main) {
  const file = h('input', { type: 'file', accept: 'application/json' });
  const rep = h('input', { type: 'checkbox' });
  main.append(h('h1', { class: 'title' }, '💾 Экспорт и импорт'),
    h('p', { class: 'sub' }, 'Конфигурация — настройки, формы, панели, должности и категории тикетов. Подходит, чтобы перенести бота на другой сервер или сохранить копию (на бесплатных хостингах диск может очищаться).'),
    h('div', { class: 'fields' },
      h('div', { class: 'field' }, h('a', { class: 'btn', href: `/api/g/${S.gid}/export`, style: 'text-decoration:none;display:inline-block' }, 'Скачать конфигурацию (.json)')),
      h('div', { class: 'field' }, h('label', { class: 'l' }, 'Загрузить конфигурацию'), file,
        h('label', { class: 'sw', style: 'margin-top:10px' }, rep, h('span', { class: 't' }), h('span', {}, 'Удалить элементы, которых нет в файле')),
        h('div', { style: 'margin-top:12px' }, h('button', { class: 'btn pri', onclick: async () => {
          if (!file.files[0]) return toast('Выберите файл', true);
          try { await api(G(`/import?replace=${rep.checked}`), { method: 'POST', body: JSON.parse(await file.files[0].text()) }); toast('Импортировано'); S.meta = await api(G('/meta')); }
          catch (e) { toast(e.message, true); }
        } }, 'Импортировать'))),
      S.me.user.owner && h('div', { class: 'field' }, h('label', { class: 'l' }, 'Резервная копия базы'), h('a', { class: 'btn', href: '/api/backup', style: 'text-decoration:none;display:inline-block' }, 'Скачать bot.db (профили, заявки, дела)'))));
}

window.addEventListener('beforeunload', e => { if (S.dirty) e.preventDefault(); });
boot();
