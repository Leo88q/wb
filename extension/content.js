/* Автозаполнение формы объявления WB Ресейл.
   Селекторы вынесены в selectors.example.json — WB меняет вёрстку, их легко поправить.
   Расширение НЕ нажимает «Опубликовать» и НЕ обходит проверки: финальный клик за вами. */
const DEFAULT_SELECTORS = {
  create_url: "https://www.wildberries.ru/",
  title: ["input[name*='name' i]", "input[placeholder*='назван' i]", "input[placeholder*='Заголов' i]", "input[type='text']"],
  description: ["textarea[name*='desc' i]", "textarea[placeholder*='опис' i]", "textarea"],
  price: ["input[name*='price' i]", "input[placeholder*='Цена' i]", "input[inputmode='numeric']"],
  size: ["input[name*='size' i]", "input[placeholder*='размер' i]"],
  condition: ["select[name*='condition' i]", "select[name*='state' i]"],
  submit_warning: "Расширение не отправляет форму. Проверьте поля и нажмите «Опубликовать» сами."
};

function nativeSet(el, value) {
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  if (setter) setter.call(el, value); else el.value = value;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.dispatchEvent(new Event('blur', {bubbles: true}));
  el.focus();
}

function find(selectors) {
  for (const s of selectors || []) {
    try { const el = document.querySelector(s); if (el) return el; } catch (e) {}
  }
  return null;
}

function badge(text, ok) {
  let b = document.getElementById('wb-resale-badge');
  if (!b) {
    b = document.createElement('div'); b.id = 'wb-resale-badge';
    b.style.cssText = 'position:fixed;z-index:99999;right:16px;bottom:16px;max-width:340px;padding:12px 16px;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.2);font:13px system-ui;color:#fff';
    document.body.appendChild(b);
  }
  b.style.background = ok ? '#6d33d6' : '#b4472e';
  b.innerText = text;
  setTimeout(() => b.remove(), 9000);
}

async function fill(item, selectors) {
  const s = {...DEFAULT_SELECTORS, ...selectors};
  const done = [], missed = [];
  const fields = [
    ['title', item.title], ['description', item.description],
    ['price', item.price ? String(item.price) : ''], ['size', item.size || ''],
  ];
  for (const [key, value] of fields) {
    if (!value) continue;
    const el = find(s[key]);
    if (el) { nativeSet(el, value); done.push(key); } else missed.push(key);
  }
  if (item.condition && s.condition) {
    const el = find(s.condition);
    if (el) {
      const opt = Array.from(el.options || []).find(o => o.text.toLowerCase().includes(String(item.condition).toLowerCase()));
      if (opt) { el.value = opt.value; el.dispatchEvent(new Event('change', {bubbles:true})); }
    }
  }
  badge(`Заполнено: ${done.join(', ') || 'ничего'}.${missed.length ? ' Не найдено: ' + missed.join(', ') + ' — вставьте вручную (текст скопирован в буфер). ' : ''} ${s.submit_warning}`, done.length > 0);
  if (missed.includes('description')) navigator.clipboard?.writeText(item.description || '');
  return {done, missed};
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg?.type !== 'WB_RESALE_FILL') return;
  fetch(chrome.runtime.getURL('selectors.json'))
    .then(r => r.ok ? r.json() : {})
    .catch(() => ({}))
    .then(sel => fill(msg.item, sel))
    .then(reply);
  return true;
});
