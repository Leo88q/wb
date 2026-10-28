const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let API = 'http://127.0.0.1:8000';

chrome.storage.local.get(['api'], r => { API = r.api || API; $('#api').value = API; load(); });
$('#save').onclick = () => { API = $('#api').value.replace(/\/$/, ''); chrome.storage.local.set({api: API}); load(); };
$('#reload').onclick = load;
$('#status').onchange = load;

async function load() {
  const status = $('#status').value;
  $('#status-line').textContent = 'Загружаю…';
  try {
    const r = await fetch(`${API}/api/listings?status=${encodeURIComponent(status)}&limit=100`);
    const data = await r.json();
    const items = data.items || [];
    $('#status-line').textContent = `Карточек: ${items.length}. Сервер: ${r.ok ? 'OK' : 'ошибка'}`;
    $('#list').innerHTML = items.slice(0, 30).map((l, i) => `
      <div class="item" data-i="${i}">
        <b>${esc(l.vendor_code)} · ${esc(l.title)}</b>
        <small>р.${esc(l.size)} · ${l.price} ₽ · ${l.photos?.length || 0} фото</small>
        <div class="acts">
          <button data-act="fill" data-i="${i}">Заполнить форму</button>
          <button class="ghost" data-act="desc" data-i="${i}">Текст</button>
          <button class="ghost" data-act="title" data-i="${i}">Заголовок</button>
          <button class="ghost" data-act="photos" data-i="${i}">Фото</button>
          <button class="ghost" data-act="published" data-i="${i}">✓ Размещено</button>
        </div>
      </div>`).join('') || '<p class="hint">Нет карточек с таким статусом.</p>';
    window.__items = items;
    $('#list').querySelectorAll('button').forEach(b => b.onclick = () => act(b.dataset.act, items[+b.dataset.i]));
  } catch (e) {
    $('#status-line').textContent = 'Не могу подключиться к локальному серверу: ' + e.message;
    $('#list').innerHTML = '<p class="hint">Запустите <code>python3 run.py</code> и проверьте адрес выше.</p>';
  }
}

function copy(text) { navigator.clipboard.writeText(text); }

async function act(action, item) {
  if (!item) return;
  if (action === 'desc') return copy(item.description || '');
  if (action === 'title') return copy(item.title || '');
  if (action === 'photos') {
    (item.photos || []).forEach(p => chrome.downloads.download({url: `${API}/data/out/${item.vendor_code}/${p}`, filename: `wb-resale/${item.vendor_code}/${p}`}));
    return;
  }
  if (action === 'published') {
    await fetch(`${API}/api/listings/${encodeURIComponent(item.vendor_code)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({status: 'published', published_at: new Date().toISOString().slice(0,10),
                            next_bump_at: new Date(Date.now()+10*86400000).toISOString().slice(0,10)})
    });
    return load();
  }
  if (action === 'fill') {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id) return;
    chrome.tabs.sendMessage(tab.id, {type: 'WB_RESALE_FILL', item}, res => {
      if (chrome.runtime.lastError) $('#status-line').textContent = 'Откройте страницу wildberries.ru и обновите её.';
    });
  }
}
