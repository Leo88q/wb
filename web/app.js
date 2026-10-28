/* WB Ресейл Ассистент — front-end без зависимостей */
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = n => new Intl.NumberFormat('ru-RU').format(Number(n || 0)) + ' ₽';
const state = { tab: 'overview', stats: {}, listings: [], queue: [], research: null, selected: null, chat: [] };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: {'Content-Type': 'application/json'}, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (!res.ok) { const e = await res.json().catch(() => ({error: res.statusText})); throw new Error(e.error || res.statusText); }
  return res.json();
}
const post = (path, body) => api(path, {method: 'POST', body});
function toast(text) { const t = $('#toast'); t.textContent = text; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 2800); }
function copy(text) { navigator.clipboard.writeText(text).then(() => toast('Скопировано')).catch(() => toast('Не удалось скопировать')); }

/* ---------- навигация ---------- */
const titles = {
  overview: ['Обзор', 'Вся воронка: от фото до поднятия объявлений'],
  listings: ['Платья', 'Карточки, описания, замеры и статусы'],
  content: ['Контент и фото', 'Импорт CSV, обработка фото, AI-описания, экспорт'],
  bumps: ['Поднятие', 'Очередь редактирований вместо «поднять»'],
  research: ['Исследование', 'Ресерч цен конкурентов и своих цен'],
  assistant: ['Ассистент', 'Помощник продавца и черновики ответов покупателям'],
};
function switchTab(name) {
  state.tab = name;
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $$('.page').forEach(p => p.classList.remove('active'));
  $('#page-' + name).classList.add('active');
  $('#page-title').textContent = titles[name][0];
  $('#page-sub').textContent = titles[name][1];
  ({overview: loadOverview, listings: loadListings, bumps: loadBumps, research: loadResearch,
    assistant: loadChatListings, content: () => {}})[name]?.();
  window.scrollTo({top: 0, behavior: 'smooth'});
}
$$('.nav-item').forEach(b => b.onclick = () => switchTab(b.dataset.tab));

/* ---------- обзор ---------- */
async function loadOverview() {
  try {
    const s = state.stats = await api('/api/stats');
    $('#nav-count').textContent = s.total;
    $('#nav-bumps').textContent = s.bumps_due || '';
    const by = s.by_status || {};
    const kpis = [
      ['Всего вещей', s.total, 'в базе'],
      ['Готово к публикации', by.ready || 0, 'есть фото и текст'],
      ['Опубликовано', by.published || 0, 'на площадке'],
      ['Поднятий в очереди', s.bumps_pending || 0, `${s.bumps_due || 0} пора сегодня`],
      ['Стоимость стока', money(s.stock_value), 'по текущим ценам'],
    ];
    $('#kpis').innerHTML = kpis.map(([a,b,c]) => `<div class="kpi"><span>${a}</span><b>${b}</b><small>${c}</small></div>`).join('');
    const rows = [['С фото', s.with_photos, '#7b2ff7'], ['С описанием', s.with_description, '#a24bff'],
      ['Опубликовано', by.published || 0, '#17a673'], ['Готово', by.ready || 0, '#e59b16'],
      ['Черновики', by.draft || 0, '#b9b1c9']];
    $('#funnel').innerHTML = rows.map(([l,v,c]) => `<div class="funnel-row"><label>${l}</label><div class="funnel-track"><div class="funnel-fill" style="width:${Math.min(100, (v/Math.max(1,s.total))*100)}%;background:${c}"></div></div><b>${v}</b></div>`).join('');
    drawCalendar('#calendar-chart', s.calendar || [], 14);
    $('#bump-summary').innerHTML = `Выполнено поднятий: <b>${s.bumps_done || 0}</b>. Интервал: <b>${s.settings.bump_interval_days} дн.</b>, по ${s.settings.bump_per_day} карточек в день.`;
    const todo = [
      `Опубликовать ${Math.min(25, by.ready || 0)} готовых карточек (не все ${s.total} сразу — модерация любит постепенность).`,
      `Выполнить ${s.bumps_pending || 0} задач из очереди поднятий: обновить текст/цену в объявлениях.`,
      `Снять фото для ${Math.max(0, s.total - s.with_photos)} вещей по шаблону: общий план, бирка, замеры, дефекты.`,
      `Сверить цены с медианой рынка во вкладке «Исследование» и включить торг.`,
      `Отвечать на вопросы покупателей в течение часа — черновики готовит ассистент.`,
    ];
    $('#todo').innerHTML = todo.map(t => `<li>${t}</li>`).join('');
    const st = s.settings;
    $('#settings').innerHTML = [
      ['Интервал поднятий, дней', 'bump_interval_days', st.bump_interval_days, 'number'],
      ['Карточек в день', 'bump_per_day', st.bump_per_day, 'number'],
      ['Шаг снижения цены, %', 'bump_price_drop_pct', st.bump_price_drop_pct, 'number'],
      ['Минимальная цена, ₽', 'min_price', st.min_price, 'number'],
      ['Снижать цену автоматически', 'bump_auto_price_drop', st.bump_auto_price_drop, 'checkbox'],
      ['Магазин / подпись', 'shop_name', st.shop_name, 'text'],
    ].map(([label, key, val, type]) => `<label>${label}${type === 'checkbox'
      ? `<div class="row"><input type="checkbox" class="input" data-setting="${key}" ${val ? 'checked' : ''}></div>`
      : `<input class="input" type="${type}" data-setting="${key}" value="${esc(val)}">`}</label>`).join('');
    $('#dot-llm').className = 'dot ' + ($('#llm-status').dataset.on === '1' ? 'on' : '');
  } catch (e) { toast('Ошибка обзора: ' + e.message); }
}
function drawCalendar(sel, data, days) {
  const max = Math.max(1, ...data.slice(0, days).map(d => d.planned));
  $(sel).innerHTML = data.slice(0, days).map(d => `<div class="bar-wrap" title="${d.date}: ${d.planned}"><div class="bar ${d.planned ? '' : 'dim'}" style="height:${Math.max(3, (d.planned/max)*100)}%"></div><small>${d.date.slice(5)}</small></div>`).join('');
}
$('#btn-save-settings').onclick = async () => {
  const patch = {};
  $$('[data-setting]').forEach(el => {
    const k = el.dataset.setting;
    patch[k] = el.type === 'checkbox' ? el.checked : (el.type === 'number' ? Number(el.value) : el.value);
  });
  await post('/api/settings', patch); toast('Настройки сохранены'); loadOverview();
};
$('#btn-export-zip').onclick = () => { window.location.href = '/api/export/zip'; toast('Готовим ZIP…'); };

/* ---------- платья ---------- */
let searchTimer;
async function loadListings() {
  const q = $('#search').value, status = $('#filter-status').value;
  const data = await api(`/api/listings?q=${encodeURIComponent(q)}&status=${encodeURIComponent(status)}&limit=500`);
  state.listings = data.items;
  const tbody = $('#listings-body');
  tbody.innerHTML = data.items.map(l => `<tr data-id="${esc(l.id)}">
    <td>${l.photos?.length ? `<img class="thumb" src="/data/out/${esc(l.vendor_code)}/${esc(l.photos[0])}" alt="">` : '<div class="no-photo">◌</div>'}</td>
    <td><div class="t-title">${esc(l.title || l.vendor_code)}</div><span class="muted">${esc(l.vendor_code)} · ${esc(l.brand)} · ${esc(l.color)}</span></td>
    <td>${esc(l.size || '—')}</td><td>${esc(l.condition || '—')}</td><td><b>${money(l.price)}</b></td>
    <td>${l.photos?.length || 0}</td><td><span class="pill ${esc(l.status)}">${esc(l.status)}</span></td>
    <td><button class="mini" data-open="${esc(l.id)}">Открыть</button></td></tr>`).join('') || '<tr><td colspan="8" class="muted">Ничего не найдено. Импортируйте CSV во вкладке «Контент и фото».</td></tr>';
  $('#listings-foot').textContent = `Показано ${data.items.length} карточек. Нажмите «Открыть», чтобы отредактировать описание, цену и статус.`;
}
$('#search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadListings, 250); };
$('#filter-status').onchange = loadListings;
$('#btn-refresh').onclick = loadListings;
$('#listings-body').onclick = e => {
  const id = e.target.dataset.open || e.target.closest('tr')?.dataset.id;
  if (id) openListing(id);
};
async function openListing(id) {
  const l = await api('/api/listings/' + encodeURIComponent(id));
  state.selected = l;
  const photos = l.photos || [];
  const sizes = await api('/api/sizes/grid');
  const ru = (l.measurements || {});
  $('#drawer-inner').innerHTML = `
    <div class="drawer-head"><h2>${esc(l.vendor_code)} · ${esc(l.brand)}</h2><button class="x" id="close-drawer">✕</button></div>
    <div class="badges"><span class="pill ${esc(l.status)}">${esc(l.status)}</span><span class="pill">размер ${esc(l.size || '—')}</span><span class="pill">${esc(l.condition || '—')}</span><span class="pill">${photos.length} фото</span>${l.is_demo ? '<span class="pill">демо</span>' : ''}</div>
    <div class="gallery">${photos.length ? photos.map(p => `<img src="/data/out/${esc(l.vendor_code)}/${esc(p)}" alt="${esc(p)}">`).join('') : '<p class="hint">Фото не загружены. Добавьте их во вкладке «Контент и фото».</p>'}</div>
    <div class="divider"></div>
    <div class="field"><label>Заголовок (для поиска)</label><input class="input" id="f-title" value="${esc(l.title)}"></div>
    <div class="field"><label>Описание объявления</label><textarea class="input" id="f-desc">${esc(l.description)}</textarea></div>
    <div class="row wrap">
      <button class="btn ghost" id="btn-regen">✦ Переписать описание</button>
      <button class="btn ghost" id="btn-copy-desc">Скопировать описание</button>
      <button class="btn ghost" id="btn-copy-title">Скопировать заголовок</button>
    </div>
    <div class="grid-2">
      <div class="field"><label>Цена, ₽</label><input class="input" type="number" id="f-price" value="${l.price}"></div>
      <div class="field"><label>Размер (RU)</label><input class="input" id="f-size" value="${esc(l.size)}"></div>
      <div class="field"><label>Состояние</label><input class="input" id="f-cond" value="${esc(l.condition)}"></div>
      <div class="field"><label>Статус</label><select class="input" id="f-status">${['draft','ready','published','sold','archived'].map(s => `<option ${s===l.status?'selected':''}>${s}</option>`).join('')}</select></div>
    </div>
    <div class="field"><label>Дефекты (честно)</label><input class="input" id="f-defects" value="${esc(l.defects)}"></div>
    <div class="field"><label>Ссылка на объявление WB</label><input class="input" id="f-url" placeholder="https://www.wildberries.ru/…" value="${esc(l.wb_url)}"></div>
    <button class="btn" id="btn-save-listing">Сохранить изменения</button>
    <div class="divider"></div>
    <h3>Размерный ряд и замеры</h3>
    <p class="hint">Замеры вещи: ${Object.entries(ru).map(([k,v]) => `${({chest:'грудь',waist:'талия',hips:'бёдра',length:'длина',sleeve:'рукав',shoulders:'плечи',hem:'подол'})[k] || k} ${v} см`).join(' · ') || 'не указаны'}.</p>
    <pre class="pre">${esc(sizes.markdown)}</pre>
    <div class="divider"></div>
    <h3>Ключевые слова</h3><div class="chips">${(l.keywords || []).map(k => `<span class="chip">${esc(k)}</span>`).join('')}</div>
    <div class="divider"></div>
    <p class="hint">Опубликовано: ${esc(l.published_at || '—')} · последнее поднятие: ${esc(l.last_bump_at || '—')} · следующее: ${esc(l.next_bump_at || '—')} · всего поднятий: ${l.bump_count || 0}</p>
  `;
  $('#drawer').classList.add('open');
  $('#close-drawer').onclick = () => $('#drawer').classList.remove('open');
  $('#btn-save-listing').onclick = async () => {
    await post('/api/listings/' + encodeURIComponent(id), {
      title: $('#f-title').value, description: $('#f-desc').value, price: Number($('#f-price').value),
      size: $('#f-size').value, condition: $('#f-cond').value, status: $('#f-status').value,
      defects: $('#f-defects').value, wb_url: $('#f-url').value,
      published_at: $('#f-status').value === 'published' && !l.published_at ? new Date().toISOString().slice(0,10) : l.published_at,
      next_bump_at: $('#f-status').value === 'published' && !l.next_bump_at ? new Date(Date.now() + 10*86400000).toISOString().slice(0,10) : l.next_bump_at,
    });
    toast('Сохранено'); loadListings(); openListing(id);
  };
  $('#btn-regen').onclick = async () => {
    const r = await post('/api/listings/' + encodeURIComponent(id) + '/regenerate', {use_llm: true});
    toast('Описание обновлено (' + r.source + ')'); loadListings(); openListing(id);
  };
  $('#btn-copy-desc').onclick = () => copy($('#f-desc').value);
  $('#btn-copy-title').onclick = () => copy($('#f-title').value);
}
$('#drawer').onclick = e => { if (e.target.id === 'drawer') e.currentTarget.classList.remove('open'); };

/* ---------- контент ---------- */
$('#csv-file').onchange = e => { const f = e.target.files[0]; if (!f) return; const r = new FileReader(); r.onload = () => $('#csv-text').value = r.result; r.readAsText(f, 'UTF-8'); };
$('#btn-example').onclick = () => {
  $('#csv-text').value = `vendor_code;brand;type;color;material;size;condition;defects;chest;waist;hips;length;sleeve;price;season;style\nDR-301;ZARA;платье миди;чёрный;вискоза;44;отличное;;88;68;94;112;25;1900;демисезон;офисное\nDR-302;MANGO;платье-футляр;бежевый;полиэстер;M;хорошее;небольшая затяжка на подоле;88;70;96;100;0;1500;круглый год;повседневное\nDR-303;LIME;платье макси;зелёный;лён;46;новое с биркой;;92;72;98;130;18;3200;лето;летнее`;
  toast('Пример вставлен');
};
$('#btn-import').onclick = async () => {
  const btn = $('#btn-import'); btn.disabled = true; btn.textContent = 'Собираю карточки…';
  try {
    const r = await post('/api/pipeline/import', {csv: $('#csv-text').value, use_llm: $('#opt-llm').checked,
      vision: $('#opt-vision').checked, process_photos: $('#opt-photos').checked});
    $('#import-result').textContent = `Строк: ${r.rows}, создано: ${r.created}, обновлено: ${r.updated}. Медиана рынка: ${r.market_median || 'нет данных'} ₽.\n${r.warnings_total ? 'Нет фото для ' + r.warnings_total + ' вещей (например ' + r.warnings.slice(0,3).join('; ') + ')' : ''}`;
    toast('Импорт завершён'); loadListings();
  } catch(e) { $('#import-result').textContent = 'Ошибка: ' + e.message; }
  btn.disabled = false; btn.textContent = 'Импортировать и собрать карточки';
};
$('#btn-clear-demo').onclick = async () => {
  if (!confirm('Удалить 300 демо-платьев и очередь поднятий? Ваши импортированные карточки останутся.')) return;
  const r = await post('/api/pipeline/clear-demo', {});
  toast(`Удалено демо-карточек: ${r.deleted}`); loadListings(); loadOverview(); loadBumps();
};
$('#btn-upload-photos').onclick = async () => {
  const vendor = $('#photo-vendor').value.trim(), files = Array.from($('#photo-files').files);
  if (!vendor || !files.length) return toast('Укажите артикул и выберите фото');
  const payload = await Promise.all(files.map(f => new Promise(res => {
    const r = new FileReader(); r.onload = () => res({name: f.name, data: r.result}); r.readAsDataURL(f);
  })));
  $('#photo-result').textContent = 'Загружаю ' + files.length + ' файлов…';
  try { const r = await post('/api/pipeline/upload-photos', {vendor_code: vendor, files: payload});
    $('#photo-result').textContent = `Сохранено ${r.saved}, готово кадров: ${r.report.photos.length}. ${(r.report.warnings || []).join(' ')}`;
    toast('Фото обработаны'); loadListings();
  } catch(e) { $('#photo-result').textContent = 'Ошибка: ' + e.message; }
};
$('#btn-reprocess').onclick = async () => { const r = await post('/api/pipeline/photos', {});
  $('#photo-result').textContent = `Обработано папок: ${r.processed}, без папок: ${r.missing_folders}`; toast('Готово'); };
$('#btn-preview').onclick = async () => {
  const r = await post('/api/generate/preview', {vendor_code: 'DEMO-1', brand: $('#preview-brand').value,
    color: $('#preview-color').value, size: $('#preview-size').value, type: 'платье миди',
    material: 'вискоза', condition: 'отличное', chest: 88, waist: 68, hips: 94, length: 112, price: 1900});
  $('#preview-out').textContent = r.title + '\n\n' + r.description + '\n\n--- Блок размеров ---\n' + r.size_block;
};
$('#steps').innerHTML = [
  ['Фото', 'Папка на каждое платье → выравнивание 3:4, 900×1200, удаление дублей и EXIF.'],
  ['Данные', 'CSV: бренд, цвет, состав, размер, состояние, дефекты, замеры.'],
  ['AI/шаблон', 'Уникальное описание + заголовок + ключевые слова; при LLM_VISION — распознавание по фото.'],
  ['Размеры', 'RU ↔ INT/EU/US, подбор по замерам, таблица размерного ряда в описание.'],
  ['Публикация', 'Экспорт JSON/CSV/ZIP → расширение Chrome или полуавто-сценарий Playwright.'],
].map(([t,d],i) => `<div class="step"><em>${i+1}</em><b>${t}</b><span>${d}</span></div>`).join('');

/* ---------- сжатие больших фото (>10 МБ) прямо в браузере ---------- */
const bigState = { rows: [], totals: null, groups: [] };
const bigLog = line => {
  const el = $('#big-log');
  const current = el.dataset.live ? el.textContent.split('\n') : [];
  current.push(line);
  el.dataset.live = '1';
  el.textContent = current.slice(-45).join('\n');
  el.scrollTop = el.scrollHeight;
};
const fmtMB = n => (n / 1048576).toFixed(2) + ' МБ';
const toJpegBlob = (canvas, q) => new Promise(res => canvas.toBlob(b => res(b), 'image/jpeg', q));
const blobToData = blob => new Promise((res, rej) => {
  const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(blob);
});
const codeFor = (prefix, i) => (prefix || 'DR').replace(/[^A-Za-z0-9_-]/g, '').slice(0, 20) + '-' + String(i).padStart(4, '0');

async function compressOne(file, tw, th, maxBytes) {
  let bmp;
  try { bmp = await createImageBitmap(file); }
  catch (e) { throw new Error('не читается браузером (HEIC/RAW?) — используйте скрипт для Mac'); }
  const canvas = document.createElement('canvas'); canvas.width = tw; canvas.height = th;
  const ctx = canvas.getContext('2d'); ctx.fillStyle = '#fafafa'; ctx.fillRect(0, 0, tw, th);
  const ratio = Math.min(tw / bmp.width, th / bmp.height);
  const w = Math.max(1, Math.round(bmp.width * ratio)), h = Math.max(1, Math.round(bmp.height * ratio));
  ctx.drawImage(bmp, Math.round((tw - w) / 2), Math.round((th - h) / 2), w, h);
  const orig = [bmp.width, bmp.height];
  if (bmp.close) bmp.close();
  let q = 0.88, blob = await toJpegBlob(canvas, q), cw = tw, ch = th;
  if (!blob) throw new Error('canvas не дал JPEG');
  while (blob.size > maxBytes && q > 0.65) { q = Math.max(0.65, q - 0.05); blob = await toJpegBlob(canvas, q); }
  while (blob.size > maxBytes && cw > 700 && ch > 900) {
    cw = Math.max(700, Math.round(cw * 0.82)); ch = Math.max(900, Math.round(ch * 0.82));
    const c2 = document.createElement('canvas'); c2.width = cw; c2.height = ch;
    c2.getContext('2d').drawImage(canvas, 0, 0, cw, ch);
    q = 0.8; blob = await toJpegBlob(c2, q);
    while (blob.size > maxBytes && q > 0.65) { q = Math.max(0.65, q - 0.05); blob = await toJpegBlob(c2, q); }
    canvas.width = cw; canvas.height = ch; canvas.getContext('2d').drawImage(c2, 0, 0);
  }
  if (blob.size > maxBytes) throw new Error('не удалось уложиться в лимит — используйте скрипт для Mac');
  return { data: await blobToData(blob), size: blob.size, quality: Math.round(q * 100),
           width: cw, height: ch, originalSize: orig };
}

function buildBigGroups(files, perItem, prefix) {
  const nested = new Map(), flat = [], groups = [];
  const nameKey = f => (f.webkitRelativePath || f.name).replace(/\\/g, '/');
  files.forEach(f => {
    const parts = nameKey(f).split('/').filter(Boolean);
    if (parts.length >= 3) {
      const key = parts.slice(1, -1).join('/');
      if (!nested.has(key)) nested.set(key, []);
      nested.get(key).push(f);
    } else flat.push(f);
  });
  const nat = (a, b) => nameKey(a).localeCompare(nameKey(b), undefined, {numeric: true});
  let i = 1;
  for (const [key, fs] of nested) {
    groups.push({vendor_code: codeFor(prefix, i++), source_folder: key, files: fs.sort(nat).slice(0, 30)});
  }
  for (let k = 0; k < flat.length; k += perItem) {
    groups.push({vendor_code: codeFor(prefix, i++), source_folder: `(пачка ${Math.floor(k / perItem) + 1})`,
                 files: flat.sort(nat).slice(k, k + perItem)});
  }
  return groups;
}

function previewBigPlan() {
  const input = $('#big-folder');
  const files = Array.from(input.files || []).filter(f => /\.(jpe?g|png|webp|heic|heif|bmp|gif|tiff?)$/i.test(f.name));
  if (!files.length) return;
  const perItem = Math.max(1, Number($('#big-per-item').value) || 2);
  const groups = buildBigGroups(files, perItem, $('#big-prefix').value);
  const naturalName = f => (f.webkitRelativePath || f.name).split('/').pop();
  const preview = groups.slice(0, 4).map(g => `${g.vendor_code}: ${g.files.map(naturalName).join(' + ')}`).join('\n');
  const extra = groups.length > 4 ? `\n… ещё ${groups.length - 4} платьев` : '';
  const odd = files.length % perItem;
  $('#big-log').dataset.live = '';
  $('#big-log').textContent = `Проверка группировки: ${files.length} фото → ${groups.length} вещей по ${perItem}.\n` +
    `Порядок: по имени файла.\n${preview}${extra}` +
    (odd ? `\n⚠️ Последняя вещь получит только ${odd} фото — всего должно быть ${perItem * groups.length} в ровных парах.` : '\nКоличество файлов делится на число фото на вещь.');
  bigState.groups = groups.map(g => ({vendor_code:g.vendor_code, source_folder:g.source_folder}));
}
$('#big-folder').addEventListener('change', previewBigPlan);
$('#big-per-item').addEventListener('input', previewBigPlan);
$('#big-prefix').addEventListener('input', previewBigPlan);

$('#btn-big-compress').onclick = async () => {
  const input = $('#big-folder');
  const files = Array.from(input.files || []).filter(f => /\.(jpe?g|png|webp|heic|heif|bmp|gif|tiff?)$/i.test(f.name));
  if (!files.length) return toast('Выберите папку с фото');
  const [tw, th] = $('#big-target').value.split('x').map(Number);
  const maxBytes = Math.round(Math.min(10, Number($('#big-max-mb').value) || 9.5) * 1048576);
  const perItem = Math.max(1, Number($('#big-per-item').value) || 2);
  const upload = $('#big-upload').checked;
  const groups = buildBigGroups(files, perItem, $('#big-prefix').value);
  bigState.groups = groups.map(g => ({vendor_code: g.vendor_code, source_folder: g.source_folder}));
  const totalBefore = files.reduce((s, f) => s + f.size, 0);
  bigState.rows = [];
  $('#big-log').textContent = ''; $('#big-log').dataset.live = '1';
  bigLog(`Найдено ${files.length} фото (${fmtMB(totalBefore)}), групп: ${groups.length}. Цель: ${tw}x${th}, лимит ${fmtMB(maxBytes)}.`);
  const btn = $('#btn-big-compress'); btn.disabled = true; btn.textContent = 'Сжимаю…';
  let done = 0, totalAfter = 0, maxFile = 0, failed = 0;

  for (const g of groups) {
    bigLog(`${g.vendor_code} ← ${g.source_folder}: ${g.files.length} фото`);
    let batch = [], append = false;
    const flush = async () => {
      if (!batch.length) return;
      if (upload) await post('/api/pipeline/upload-ready', {
        groups: [{vendor_code: g.vendor_code, source_folder: g.source_folder, files: batch, append}]
      });
      batch = []; append = true;
    };
    for (const f of g.files) {
      try {
        const c = await compressOne(f, tw, th, maxBytes);
        batch.push({name: f.name, data: c.data, original: f.size});
        totalAfter += c.size; maxFile = Math.max(maxFile, c.size);
        bigState.rows.push({item: g.vendor_code, source_folder: g.source_folder, original: f.name,
          original_mb: (f.size / 1048576).toFixed(2), output_mb: (c.size / 1048576).toFixed(3),
          size: `${c.width}x${c.height}`, quality: c.quality, status: 'OK'});
        if (batch.length >= 4) await flush();
      } catch (e) {
        failed++;
        bigState.rows.push({item: g.vendor_code, source_folder: g.source_folder, original: f.name,
          original_mb: (f.size / 1048576).toFixed(2), output_mb: '', size: '', quality: '',
          status: 'ОШИБКА: ' + e.message});
        bigLog(`  ⚠ ${f.name}: ${e.message}`);
      }
      done++;
      $('#big-bar').style.width = Math.round(done / files.length * 100) + '%';
    }
    await flush();
  }
  const saved = totalBefore ? Math.round((1 - totalAfter / totalBefore) * 100) : 0;
  bigState.totals = {totalBefore, totalAfter, maxFile, failed, groups: groups.length, files: files.length};
  $('#big-result').innerHTML = `<b>Готово.</b> Групп (платьев): ${groups.length}, файлов: ${files.length - failed}. ` +
    `Было <b>${fmtMB(totalBefore)}</b> → стало <b>${fmtMB(totalAfter)}</b> (экономия ${saved}%). ` +
    `Максимальный файл <b>${fmtMB(maxFile)}</b> (лимит ${fmtMB(maxBytes)}). Ошибок: ${failed}.` +
    (upload ? '<br>Карточки появились во вкладке «Платья» со статусом <i>draft</i> — загрузите CSV, чтобы добавить описания и размеры.' : '<br>Загрузка в базу отключена — файлы не сохранены.');
  bigLog('✅ Завершено. ' + (failed ? 'Часть файлов требует скрипта для Mac (HEIC).' : 'Все файлы в норме.'));
  btn.disabled = false; btn.textContent = 'Сжать и загрузить';
  toast('Сжатие завершено');
  if (upload) { loadListings(); loadOverview(); }
};

$('#btn-big-inventory').onclick = () => {
  if (!bigState.groups.length) return toast('Сначала выберите папку с фото');
  const head = ['vendor_code','source_folder','photo_count','brand','type','color','print','material','size',
    'condition','defects','chest','waist','hips','length','sleeve','shoulders','price','season','style','notes'];
  const okCounts = {};
  bigState.rows.filter(r => r.status === 'OK').forEach(r => { okCounts[r.item] = (okCounts[r.item] || 0) + 1; });
  const q = value => `"${String(value ?? '').replace(/"/g, '""')}"`;
  const lines = [head.join(';'), ...bigState.groups.map(g => {
    const row = {vendor_code:g.vendor_code, source_folder:g.source_folder, photo_count:okCounts[g.vendor_code] || 0,
                 type:'платье', notes:'Заполните факты по ярлыку и осмотру; неизвестное не угадывать.'};
    return head.map(h => q(row[h] || '')).join(';');
  })];
  const url = URL.createObjectURL(new Blob(['\ufeff' + lines.join('\n')], {type:'text/csv;charset=utf-8'}));
  const a = document.createElement('a'); a.href = url; a.download = 'inventory.csv'; a.click(); URL.revokeObjectURL(url);
};

$('#btn-big-report').onclick = () => {
  if (!bigState.rows.length) return toast('Сначала сожмите фото');
  const head = ['item', 'source_folder', 'original', 'original_mb', 'output_mb', 'size', 'quality', 'status'];
  const lines = [head.join(';'), ...bigState.rows.map(r => head.map(h => `"${String(r[h] ?? '').replace(/"/g, '""')}"`).join(';'))];
  const url = URL.createObjectURL(new Blob(['\ufeff' + lines.join('\n')], {type: 'text/csv;charset=utf-8'}));
  const a = document.createElement('a'); a.href = url; a.download = 'photo_compress_report.csv'; a.click();
  URL.revokeObjectURL(url);
};

/* ---------- поднятия ---------- */
async function loadBumps() {
  const [all, meta] = await Promise.all([api('/api/bumps/tasks'), api('/api/bumps/queue')]);
  state.queue = all.items;
  const d = meta, s = meta.settings;
  $('#bump-per-day').value = s.bump_per_day;
  if (!$('#bump-date').value) $('#bump-date').value = new Date().toISOString().slice(0,10);
  drawCalendar('#bump-calendar', d.calendar || [], 21);
  renderQueue();
}
function renderQueue() {
  const filter = $('#bump-filter').value;
  const items = state.queue.filter(t => !filter || t.status === filter);
  $('#queue-count').textContent = items.length;
  $('#bump-queue').innerHTML = items.map(t => `<div class="task ${t.status === 'done' ? 'done' : ''}">
    <h4>${esc(t.vendor_code)} · ${esc(t.title || '')}</h4>
    <div class="meta">${esc(t.due_at)} · ${t.kind === 'price_drop' ? `снизить цену ${money(t.price)} → ${money(t.suggested_price)}` : 'обновить текст'} · ${esc(t.status)}</div>
    <div class="note">${esc(t.note)}</div>
    <textarea readonly>${esc(t.suggested_description)}</textarea>
    <div class="row wrap" style="margin-top:8px">
      ${t.status === 'pending' ? `<button class="mini" data-done="${t.id}">✓ Выполнено</button><button class="mini" data-skip="${t.id}">Пропустить</button>` : ''}
      <button class="mini" data-copydesc="${t.id}">Скопировать новый текст</button>
      ${t.wb_url ? `<a class="mini" href="${esc(t.wb_url)}" target="_blank">Открыть на WB</a>` : ''}
    </div></div>`).join('') || '<p class="hint">Задач нет. Нажмите «Сгенерировать задачи на день».</p>';
}
$('#btn-gen-bumps').onclick = async () => {
  const r = await post('/api/bumps/generate', {date: $('#bump-date').value || undefined, per_day: Number($('#bump-per-day').value) || undefined});
  $('#bump-gen-result').textContent = `Создано задач: ${r.created} (из ${r.candidates} кандидатов). Интервал ${r.interval_days} дн. ${r.hint}`;
  toast('Очередь готова'); loadBumps(); loadOverview();
};
$('#btn-clear-bumps').onclick = async () => { await post('/api/bumps/clear', {}); toast('Очередь очищена'); loadBumps(); };
$('#bump-filter').onchange = renderQueue;
$('#bump-queue').onclick = async e => {
  const done = e.target.dataset.done, skip = e.target.dataset.skip, cp = e.target.dataset.copydesc;
  if (done) { await post('/api/bumps/complete', {id: Number(done)}); toast('Отмечено, срок продлён'); loadBumps(); loadOverview(); }
  if (skip) { await post('/api/bumps/skip', {id: Number(skip)}); loadBumps(); }
  if (cp) { const t = state.queue.find(x => x.id === Number(cp)); if (t) copy(t.suggested_description); }
};

/* ---------- исследование ---------- */
$('#market-file').onchange = e => { const f = e.target.files[0]; if (f) { const r = new FileReader(); r.onload = () => $('#market-text').value = r.result; r.readAsText(f, 'UTF-8'); } };
$('#btn-market-demo').onclick = () => {
  let rows = ['title;price;condition'];
  const brands = ['ZARA','MANGO','H&M','LIME','BEFREE','COS','SELA'];
  for (let i=0;i<120;i++) { const b = brands[i%brands.length]; const p = 700 + (i*137)%4200;
    rows.push(`Платье ${b} б/у размер ${40 + (i%8)*2} миди;${p};${i%3===0?'хорошее':'отличное'}`); }
  $('#market-text').value = rows.join('\n'); toast('Пример данных готов');
};
$('#btn-market-import').onclick = async () => {
  try { const r = await post('/api/research/import', {csv: $('#market-text').value});
    $('#market-result').textContent = `Загружено ${r.rows} объявлений. Медиана ${r.analysis.median} ₽.`; toast('Данные сохранены'); loadResearch();
  } catch(e) { $('#market-result').textContent = 'Ошибка: ' + e.message; }
};
$('#market-query').onchange = loadResearch;
async function loadResearch() {
  const data = await api('/api/research?query=' + encodeURIComponent($('#market-query').value));
  state.research = data.analysis; const a = data.analysis;
  if (!data.has_data) {
    $('#market-stats').innerHTML = '<p class="hint">Данных нет — загрузите CSV конкурентов или нажмите «Пример данных».</p>';
    $('#market-chart').innerHTML = ''; $('#market-words').innerHTML = ''; $('#market-sample').innerHTML = ''; return;
  }
  const stats = [['Объявлений', a.total], ['Медиана', money(a.query_median ?? a.median)], ['Нижний квартиль', money(a.query_p25 ?? a.p25)],
    ['Верхний квартиль', money(a.query_p75 ?? a.p75)], ['Минимум', money(a.min)], ['Максимум', money(a.max)]];
  $('#market-stats').innerHTML = stats.map(([l,v]) => `<div class="mstat"><span>${l}</span><b>${v}</b></div>`).join('');
  const points = [a.min, a.p25, a.median, a.p75, a.max].filter(x => x != null);
  const max = Math.max(1, ...points);
  $('#market-chart').innerHTML = points.map((v,i) => `<div class="bar-wrap" title="${v} ₽"><div class="bar" style="height:${(v/max)*100}%"></div><small>${['min','p25','med','p75','max'][i]}</small></div>`).join('');
  $('#market-words').innerHTML = (a.top_words || []).map(w => `<span class="chip click" data-word="${esc(w.word)}">${esc(w.word)} <b>${w.count}</b></span>`).join('');
  $('#market-sample').innerHTML = (a.sample || []).map(s => `<div><span>${esc(s.title)}</span><b>${money(s.price)}</b></div>`).join('');
  $$('#market-words .chip').forEach(c => c.onclick = () => { $('#market-query').value = c.dataset.word; loadResearch(); });
}
$('#btn-reprice').onclick = async () => { const r = await post('/api/research/reprice', {query: $('#market-query').value});
  toast(`Пересчитано цен для ${r.updated} вещей (медиана ${r.market_median} ₽)`); };

/* ---------- ассистент ---------- */
const prompts = ['Как автоматизировать выгрузку 300 платьев?', 'Что делать сегодня?', 'Как поднимать карточки?',
  'Посоветуй цену', 'Перепиши описание', 'Какие правила у Ресейла?', 'Расскажи про размерный ряд'];
$('#chat-prompts').innerHTML = prompts.map(p => `<span class="chip click" data-prompt="${esc(p)}">${esc(p)}</span>`).join('');
$$('#chat-prompts .chip').forEach(c => c.onclick = () => { $('#chat-text').value = c.dataset.prompt; sendChat(); });
async function loadChatListings() {
  const d = await api('/api/listings?limit=500');
  $('#chat-listing').innerHTML = '<option value="">Вещь не выбрана</option>' + d.items.map(l => `<option value="${esc(l.id)}">${esc(l.vendor_code)} — ${esc(l.brand)} р.${esc(l.size)}</option>`).join('');
  if (state.selected) $('#chat-listing').value = state.selected.id;
}
function pushMsg(role, text, source) {
  state.chat.push({role, text, source});
  $('#chat').innerHTML = state.chat.map(m => `<div class="msg ${m.role}">${esc(m.text)}${m.source ? `<small>${esc(m.source)}</small>` : ''}</div>`).join('');
  $('#chat').scrollTop = $('#chat').scrollHeight;
}
async function sendChat() {
  const text = $('#chat-text').value.trim(); if (!text) return;
  $('#chat-text').value = ''; pushMsg('user', text);
  try {
    const r = await post('/api/assistant', {message: text, mode: $('#chat-mode').value, listing_id: $('#chat-listing').value || null});
    pushMsg('bot', r.reply, r.source);
    (r.actions || []).forEach(a => { if (a.action === 'tab') toast('Откройте вкладку: ' + a.payload); });
    if (/обновил карточку/i.test(r.reply)) loadListings();
  } catch(e) { pushMsg('bot', 'Ошибка: ' + e.message); }
}
$('#btn-chat').onclick = sendChat;
$('#chat-text').onkeydown = e => { if (e.key === 'Enter') sendChat(); };
$('#quick-actions').innerHTML = [
  ['Сгенерировать очередь поднятий', 'bumps'], ['Импортировать CSV', 'content'],
  ['Посмотреть карточки', 'listings'], ['Исследование цен', 'research'], ['Экспорт ZIP', 'export'],
].map(([t,a]) => `<button data-go="${a}">${t} →</button>`).join('');
$$('#quick-actions button').forEach(b => b.onclick = () => {
  const a = b.dataset.go; if (a === 'export') return window.location.href = '/api/export/zip'; switchTab(a);
});

/* ---------- справка ---------- */
$('#btn-help').onclick = () => {
  $('#modal-card').innerHTML = `<h2>Как это работает</h2>
  <p><b>1. Подготовка.</b> Складываете фото в <code>data/incoming/АРТИКУЛ/</code>, заполняете CSV. Система обрабатывает фото, считает размерный ряд и генерирует уникальные описания (офлайн-шаблоны или LLM).</p>
  <p><b>2. Публикация.</b> У WB Ресейл (C2C) нет публичного API — объявление создаётся вручную на сайте/в приложении. Выгрузка JSON/CSV/ZIP + расширение Chrome заполняют поля формы за секунды; фото перетаскиваются папкой. Полностью авто-публикация возможна только через UI-автоматизацию (Playwright) — это риск по правилам WB, используйте на свой страх и риск.</p>
  <p><b>3. Поднятие.</b> Кнопки «Поднять» нет. Редактирование объявления продлевает 2-месячный срок и освежает карточку; снижение цены пушует интересовавшимся покупателям. Планировщик готовит по 10–15 задач в день с готовым текстом и ценой.</p>
  <p><b>4. Ассистент.</b> Помогает по процессу, переписывает описания, считает цены и готовит ответы на вопросы покупателей по фактам вашей карточки.</p>
  <p class="hint">Все данные хранятся локально в <code>data/wb_resale.db</code>. Ключ LLM не обязателен: без него работает офлайн-генератор.</p>
  <button class="btn" onclick="document.getElementById('modal').classList.remove('open')">Понятно</button>`;
  $('#modal').classList.add('open');
};
$('#modal').onclick = e => { if (e.target.id === 'modal') e.currentTarget.classList.remove('open'); };

/* ---------- старт ---------- */
(async function init() {
  try {
    const h = await api('/api/health');
    $('#llm-status').textContent = h.llm ? 'LLM подключён' : 'LLM не задан (офлайн-шаблоны)';
    $('#llm-status').dataset.on = h.llm ? '1' : '0';
    $('#dot-llm').classList.toggle('on', h.llm);
    $('#llm-state').textContent = h.llm ? 'AI-генерация активна' : 'Работают офлайн-шаблоны';
    if (!h.pillow) toast('Pillow не установлен — фото копируются без обработки');
  } catch(e) { toast('Сервер недоступен: ' + e.message); }
  pushMsg('bot', 'Здравствуйте! Я ассистент по ресейлу платьев. Спросите, как автоматизировать выгрузку, как поднимать карточки или попросите переписать описание (выберите вещь справа).', 'offline');
  loadOverview(); loadListings(); loadChatListings();
})();
