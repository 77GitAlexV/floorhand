// Offline marks and phone speech for the worker screens. No build step, no globals
// except window.FloorhandQueue and window.FloorhandVoice.
//
// Rules (from a working shop ERP, where queued marks were lost):
// - every tap gets its own idem_key and the phone time of the tap (created_at);
// - a mark leaves the queue only when the server says "recorded" or "already";
// - a refusal is never dropped silently: it moves to `attention` with the reason;
// - 401/403, 5xx and no network keep the mark queued and retry later;
// - on the shared tablet each queued tap carries the person who was signed in.
(function () {
  const KEY = "fh_mark_queue_v1";
  const ATTN = "fh_mark_attention_v1";
  const listeners = new Set();
  let busy = false;
  let timer = null;

  function load(key) {
    try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch (e) { return []; }
  }
  function save(key, list) {
    try { localStorage.setItem(key, JSON.stringify(list)); } catch (e) { /* private mode */ }
  }
  let queue = load(KEY);
  let attention = load(ATTN);

  function idem() {
    const r = (crypto && crypto.randomUUID) ? crypto.randomUUID()
      : Date.now().toString(36) + Math.random().toString(36).slice(2);
    return "q-" + r;
  }
  function changed() {
    save(KEY, queue);
    save(ATTN, attention);
    listeners.forEach(fn => { try { fn(state()); } catch (e) { /* screen gone */ } });
  }
  function state() {
    return { waiting: queue.length, attention: attention.slice() };
  }

  // item: {code} | {unit_id} | {order_id} | {kind: "undo", event_id}; optional method,
  // for_date, person_id (shared tablet). Returns the idem_key.
  function add(item) {
    const it = Object.assign({ idem_key: idem(), created_at: new Date().toISOString() }, item);
    queue.push(it);
    changed();
    flush();
    return it.idem_key;
  }

  async function flush() {
    if (busy || !queue.length) return;
    if (navigator.onLine === false) return schedule(15000);
    busy = true;
    const batch = queue.slice(0, 200);
    try {
      const res = await fetch("/api/marking/sync", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: batch }),
      });
      if (!res.ok) return schedule(res.status >= 500 ? 20000 : 60000);
      const data = await res.json();
      const byKey = new Map(data.results.map(r => [r.idem_key, r]));
      queue = queue.filter(it => {
        const r = byKey.get(it.idem_key);
        if (!r) return true;
        if (r.status === "refused") {
          attention.push({ item: it, message: r.message, needs_confirm: !!r.needs_confirm });
        }
        return false;
      });
      changed();
      if (queue.length) schedule(1000);
    } catch (e) {
      schedule(15000);  // no network: try again later
    } finally {
      busy = false;
    }
  }

  function schedule(ms) {
    clearTimeout(timer);
    timer = setTimeout(flush, ms);
  }

  // Send a refused mark again, e.g. confirmed "mark the next piece too" or after the
  // previous stage caught up. Keeps the same idem_key, so it can never record twice.
  function retry(idemKey, extra) {
    const i = attention.findIndex(a => a.item.idem_key === idemKey);
    if (i < 0) return;
    const it = Object.assign({}, attention[i].item, extra || {});
    attention.splice(i, 1);
    queue.push(it);
    changed();
    flush();
  }
  function dismiss(idemKey) {
    attention = attention.filter(a => a.item.idem_key !== idemKey);
    changed();
  }

  addEventListener("online", flush);
  addEventListener("visibilitychange", () => { if (!document.hidden) flush(); });
  setTimeout(flush, 500);

  window.FloorhandQueue = {
    add, flush, retry, dismiss, state,
    onChange(fn) { listeners.add(fn); fn(state()); return () => listeners.delete(fn); },
  };

  // The phone's own speech recognition: the zero-cost path for voice marks. The words
  // go to /api/marking/voice/text, which only proposes ("Understood as ... [Yes] [No]").
  window.FloorhandVoice = {
    available() { return !!(window.SpeechRecognition || window.webkitSpeechRecognition); },
    listen(lang) {
      return new Promise((resolve, reject) => {
        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SR) return reject(new Error("no-speech"));
        const rec = new SR();
        rec.lang = lang === "ru" ? "ru-RU" : "en-US";
        rec.interimResults = false;
        rec.maxAlternatives = 1;
        rec.onresult = e => resolve(e.results[0][0].transcript);
        rec.onerror = e => reject(new Error(e.error || "speech"));
        rec.onend = () => reject(new Error("silence"));
        rec.start();
      });
    },
  };
})();
