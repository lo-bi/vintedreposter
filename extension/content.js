// Content script for Vinted member page: inject a 'Republish' button next to 'Booster'

(function () {
  const ITEM_CARD_SELECTOR = '[data-testid^="product-item-id-"]';
  const BOOST_BUTTON_SELECTOR = 'button[data-testid="bump-button"]';

  function log(...args) { console.debug('[Vinted Cloner]', ...args); }

  // Cache created-at labels per item id to avoid repeated fetch/inserts
  const createdAtCache = new Map();
  // Cache of full wardrobe items returned via our own fetch
  const wardrobeItemMap = new Map();
  let wardrobeFetched = false;

  function ensureStyles() {
    if (document.getElementById('vinted-republish-style')) return;
    const style = document.createElement('style');
    style.id = 'vinted-republish-style';
    style.textContent = `
      .vinted-republish-btn { display: block !important; margin-top: 8px !important; }
      .vinted-republish-btn.is-loading { opacity: 0.6; pointer-events: none; }
    `;
    document.head.appendChild(style);
  }

  function findClosestItemId(el) {
    // Walk up to find an ancestor with data-testid like product-item-id-<id>--*
    let cur = el;
    while (cur && cur !== document.body) {
      const testid = cur.getAttribute && cur.getAttribute('data-testid');
      if (testid && testid.startsWith('product-item-id-')) {
        const m = testid.match(/^product-item-id-(\d+)/);
        if (m) return m[1];
      }
      cur = cur.parentElement;
    }
    // Fallback: try nearest descendant
    const node = el.closest ? el.closest(ITEM_CARD_SELECTOR) : null;
    if (node) {
      const tid = node.getAttribute('data-testid') || '';
      const m = tid.match(/^product-item-id-(\d+)/);
      if (m) return m[1];
    }
    return null;
  }

  function makeButton(label) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'web_ui__Button__button web_ui__Button__outlined web_ui__Button__small web_ui__Button__primary web_ui__Button__truncated';
    const span1 = document.createElement('span');
    span1.className = 'web_ui__Button__content';
    const span2 = document.createElement('span');
    span2.className = 'web_ui__Button__label';
    span2.textContent = label;
    span1.appendChild(span2);
    btn.appendChild(span1);
    return btn;
  }

  async function fetchJson(url, opts = {}) {
    const res = await fetch(url, opts);
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`HTTP ${res.status}: ${txt}`);
    }
    return res.json();
  }

  function getCsrfFromHtml(html) {
    const m = html.match(/"CSRF_TOKEN"\s*:\s*"([^"]+)"/);
    return m ? m[1] : null;
  }

  function getCookie(name) {
    try {
      const parts = document.cookie.split(';').map(s => s.trim());
      // If duplicates exist, prefer the last occurrence
      for (let i = parts.length - 1; i >= 0; i--) {
        const p = parts[i];
        if (!p) continue;
        const eq = p.indexOf('=');
        if (eq > 0) {
          const k = decodeURIComponent(p.slice(0, eq).trim());
          if (k === name) return decodeURIComponent(p.slice(eq + 1));
        }
      }
    } catch (e) {}
    return null;
  }

  function getCsrfFromDOM() {
    try {
      const html = document.documentElement && document.documentElement.innerHTML;
      if (!html) return null;
      return getCsrfFromHtml(html);
    } catch (e) {
      return null;
    }
  }

  async function getTokensFromBg() {
    return new Promise(resolve => {
      try {
        chrome.runtime.sendMessage({ type: 'vinted:getTokens' }, res => {
          resolve(res || {});
        });
      } catch (e) {
        resolve({});
      }
    });
  }

  // Extract member id from URL or cookie
  function getMemberId() {
    try {
      const m = (location.pathname || '').match(/\/member\/(\d+)/);
      if (m) return m[1];
    } catch (_) {}
    const vuid = getCookie('v_uid');
    return vuid && /^(\d+)$/.test(vuid) ? vuid : null;
  }

  async function fetchWardrobeAndAnnotate() {
    if (wardrobeFetched) return;
    const memberId = getMemberId();
    if (!memberId) return;
    wardrobeFetched = true;
    let csrf = null;
    try { csrf = await getCsrf(); } catch (_) { csrf = null; }
    let anonId = getCookie('anon_id');
    if (!anonId) {
      try { const bg = await getTokensFromBg(); anonId = (bg && bg.anonId) || null; } catch (_) {}
    }
    const params = new URLSearchParams({ page: '1', per_page: '20', order: 'relevance' }).toString();
    try {
      const res = await fetch(`https://www.vinted.fr/api/v2/wardrobe/${memberId}/items?${params}`, {
        credentials: 'include',
        headers: {
          'accept': 'application/json, text/plain, */*',
          ...(csrf ? { 'x-csrf-token': csrf } : {}),
          ...(anonId ? { 'x-anon-id': anonId } : {}),
          'referer': location.href,
        },
      });
      if (!res.ok) return;
      const j = await res.json().catch(() => null);
      const items = (j && j.items) || [];
      for (const it of items) {
        const id = it && it.id; if (!id) continue;
        wardrobeItemMap.set(String(id), it);
        const comp = computeDaysFromPhotos(it);
        if (!comp) continue;
        const label = formatDaysFr(comp.days);
        createdAtCache.set(String(id), label);
      }
      // After caching labels, try to render them in the current DOM
      applyCreatedLabels();
    } catch (_) {
      // ignore fetch errors
    }
  }

  function getDescriptionContainerById(id) {
    // Primary selector (as per provided HTML): data-testid="product-item-id-<id>--description--content"
    const exact = document.querySelector(`[data-testid="product-item-id-${id}--description--content"]`);
    if (exact) return exact;
    // Fallback older layout
    const compat = document.querySelector(`[data-testid="product-item-id-${id}--description"] .web_ui__Cell__body`);
    if (compat) return compat;
    return null;
  }

  function computeDaysFromPhotos(item) {
    const photos = (item && item.photos) || [];
    let ts = null;
    for (const p of photos) {
      const hr = p && p.high_resolution;
      let t = hr && hr.timestamp;
      if (t != null) {
        const num = typeof t === 'number' ? t : parseInt(String(t), 10);
        if (!Number.isNaN(num)) {
          ts = ts == null ? num : Math.min(ts, num);
        }
      }
    }
    if (ts == null) return null;
    // Timestamps appear to be epoch seconds in examples
    const created = new Date(ts * 1000);
    const now = new Date();
    const days = Math.max(0, Math.floor((now - created) / 86400000));
    return { days, created };
  }

  function formatDaysFr(days) {
    if (days <= 0) return "Aujourd’hui";
    if (days === 1) return "Il y a 1 jour";
    return `il y a ${days} jours`;
  }

  function insertCreatedAtLine(container, label) {
    if (!container) return;
    if (container.querySelector('.vinted-created-at')) return; // avoid duplicates
    const wrap = document.createElement('div');
    wrap.className = 'new-item-box__description vinted-created-at';
    const p = document.createElement('p');
    p.className = 'web_ui__Text__text web_ui__Text__caption web_ui__Text__left web_ui__Text__truncated';
    p.textContent = label;
    wrap.appendChild(p);
    container.appendChild(wrap);
  }

  function applyCreatedLabels() {
    for (const [id, label] of createdAtCache.entries()) {
      const desc = getDescriptionContainerById(id);
      if (desc) insertCreatedAtLine(desc, label);
    }
  }

  async function getCsrf() {
    // 1) Try to parse CSRF directly from current member page DOM
    const fromDom = getCsrfFromDOM();
    if (fromDom) return fromDom;
    // 2) Try to reuse CSRF captured from other requests (background)
    const fromBg = await getTokensFromBg();
    if (fromBg && fromBg.csrf) return fromBg.csrf;
    // 3) Fallback: load items/new and parse token from HTML
    const res = await fetch('https://www.vinted.fr/items/new', { credentials: 'include' });
    const html = await res.text();
    const token = getCsrfFromHtml(html);
    if (!token) throw new Error('Could not extract CSRF token');
    return token;
  }

  async function getItemDetails(itemId, csrf) {
    const { anonId } = await getTokensFromBg();
    // Try editor endpoint first (richer, expects CSRF)
    try {
      const res = await fetch(`https://www.vinted.fr/api/v2/item_upload/items/${itemId}`, {
        credentials: 'include',
        headers: {
          'x-csrf-token': csrf,
          'x-enable-multiple-size-groups': 'true',
          ...(anonId ? { 'x-anon-id': anonId } : {}),
          'accept': 'application/json, text/plain, */*'
        },
      });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(`HTTP ${res.status}: ${txt}`);
      }
      const j = await res.json();
      return j.item || j || {};
    } catch (e) {
      // Fallback to public item endpoint, still pass CSRF/anon when available
      const res = await fetch(`https://www.vinted.fr/api/v2/items/${itemId}`, {
        credentials: 'include',
        headers: {
          ...(csrf ? { 'x-csrf-token': csrf } : {}),
          ...(anonId ? { 'x-anon-id': anonId } : {}),
          'accept': 'application/json, text/plain, */*'
        },
      });
      if (!res.ok) {
        const txt = await res.text();
        // Surface Cloudflare/DataDome challenge clearly
        if (res.status === 403 && /__cf_chl|cf_chl/.test(txt || '')) {
          throw new Error('Blocked by Cloudflare/DataDome (403). Refresh the page and try again.');
        }
        throw new Error(`HTTP ${res.status}: ${txt}`);
      }
      const j = await res.json();
      return j.item || {};
    }
  }

  function pickPhotos(src) {
    const out = [];
    const photos = src.photos || [];
    for (const p of photos) {
      const u = p.full_size_url || p.url || (p.thumbnails && p.thumbnails[0] && p.thumbnails[0].url);
      if (u) out.push(u);
    }
    return out;
  }

  async function downloadAsBlob(url) {
    // Try without credentials first; CDN images are public but CORS may block with include
    try {
      const res = await fetch(url, { credentials: 'omit', mode: 'cors' });
      if (res.ok) return res.blob();
      // fallthrough to background fetch
    } catch (_) {}
    // Use background service worker to fetch as ArrayBuffer and reconstruct Blob
    const bgRes = await new Promise(resolve => {
      try {
        chrome.runtime.sendMessage({ type: 'vinted:fetchArrayBuffer', url }, resolve);
      } catch (e) {
        resolve({ ok: false, error: e && e.message });
      }
    });
    if (!bgRes || !bgRes.ok || !bgRes.buffer) {
      throw new Error(`Failed to download via background: ${url} ${bgRes && (bgRes.status || bgRes.error) || ''}`);
    }
    try {
      const buf = bgRes.buffer;
      // In MV3, structured cloning supports ArrayBuffer transfer
      const contentType = bgRes.contentType || 'image/jpeg';
      return new Blob([buf], { type: contentType });
    } catch (e) {
      // Fallback: force JPEG
      return new Blob([bgRes.buffer], { type: 'image/jpeg' });
    }
  }

  async function uploadPhoto(csrf, file, tempUuid) {
    const fd = new FormData();
    fd.append('photo[type]', 'item');
    fd.append('photo[temp_uuid]', tempUuid);
    fd.append('photo[file]', file, 'photo.jpg');
    let anonId = getCookie('anon_id');
    if (!anonId) {
      const bg = await getTokensFromBg();
      anonId = (bg && bg.anonId) || null;
    }
    const res = await fetch('https://www.vinted.fr/api/v2/photos', {
      method: 'POST',
      body: fd,
      credentials: 'include',
      headers: {
        'x-csrf-token': csrf,
        'x-enable-multiple-size-groups': 'true',
        ...(anonId ? { 'x-anon-id': anonId } : {}),
      },
    });
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
    return res.json();
  }

  async function createItem(csrf, payload) {
    let anonId = getCookie('anon_id');
    if (!anonId) {
      const bg = await getTokensFromBg();
      anonId = (bg && bg.anonId) || null;
    }
    const res = await fetch('https://www.vinted.fr/api/v2/item_upload/items', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'content-type': 'application/json',
        'x-csrf-token': csrf,
        'x-enable-multiple-size-groups': 'true',
        'x-upload-form': 'true',
        ...(anonId ? { 'x-anon-id': anonId } : {}),
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let body = '';
      try { body = await res.text(); } catch (_) {}
      throw new Error(`Create failed: ${res.status} ${body}`);
    }
    return res.json();
  }

  async function deleteItem(csrf, itemId) {
    let anonId = getCookie('anon_id');
    if (!anonId) {
      const bg = await getTokensFromBg();
      anonId = (bg && bg.anonId) || null;
    }
    const res = await fetch(`https://www.vinted.fr/api/v2/items/${itemId}/delete`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'x-csrf-token': csrf,
        ...(anonId ? { 'x-anon-id': anonId } : {}),
        'accept': 'application/json, text/plain, */*',
      },
    });
    if (!res.ok) {
      let body = '';
      try { body = await res.text(); } catch (_) {}
      throw new Error(`Delete failed: ${res.status} ${body}`);
    }
    try { return await res.json(); } catch (_) { return { ok: true }; }
  }

  function uuidv4() {
    // RFC4122-ish using crypto.getRandomValues
    return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
      (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
    );
  }

  async function republishFromButton(btn) {
    const itemId = findClosestItemId(btn);
    if (!itemId) {
      alert('Could not determine item id');
      return;
    }
    try {
  btn.disabled = true; btn.classList.add('is-loading'); btn.textContent = 'Republishing…';
  const csrf = await getCsrf();
  const base = await getItemDetails(itemId, csrf);
  const tempUuid = uuidv4();
      const photoUrls = pickPhotos(base);
      const assigned = [];
      for (const u of photoUrls) {
        try {
          const blob = await downloadAsBlob(u);
          const up = await uploadPhoto(csrf, blob, tempUuid);
          if (up && up.id) assigned.push({ id: up.id, orientation: up.orientation || 0 });
        } catch (e) { log('photo upload failed', e); }
      }
    const priceObj = base.price || {};
    const priceNum = base.price_numeric || parseFloat(priceObj.amount || '0') || 0;
    const currency = base.price_currency || priceObj.currency_code || base.currency || 'EUR';
  const colorIds = base.color_ids || [base.color1_id, base.color2_id].filter(Boolean);
  const brandTitleRaw = base.brand_title || base.brand || (base.brand_dto && base.brand_dto.title) || null;
  const brandTitle = (brandTitleRaw && String(brandTitleRaw).trim()) ? brandTitleRaw : null;
      const payload = {
        item: {
          id: null,
          currency,
          temp_uuid: tempUuid,
          title: base.title || '',
          description: base.description || '',
          brand_id: base.brand_id || null,
      brand: brandTitle,
          size_id: base.size_id || null,
          catalog_id: base.catalog_id || null,
      // optional book/media fields
      isbn: base.isbn || null,
      author: base.author || null,
      book_title: base.book_title || null,
      model: base.model || null,
      video_game_rating_id: base.video_game_rating_id || null,
          is_unisex: Boolean(base.is_unisex),
          status_id: base.status_id || 1,
          price: priceNum,
          package_size_id: base.package_size_id || 1,
          shipment_prices: { domestic: null, international: null },
      color_ids: colorIds || [],
          assigned_photos: assigned,
          item_attributes: base.item_attributes || [],
          manufacturer: base.manufacturer || null,
          manufacturer_labelling: base.manufacturer_labelling || null,
      // measurements if present
      measurement_length: base.measurement_length != null ? base.measurement_length : null,
      measurement_width: base.measurement_width != null ? base.measurement_width : null,
    measurement_unit: base.measurement_unit != null ? base.measurement_unit : null,
        },
        feedback_id: null,
        push_up: false,
        parcel: null,
        upload_session_id: tempUuid,
      };
      // Build a summary of the original (to show if create fails after deletion)
      const deletedSummary = {
        id: base.id || itemId,
        title: base.title || '',
        description: base.description || '',
        price: priceNum,
        currency,
        brand_id: base.brand_id || null,
        brand: brandTitle,
        size_id: base.size_id || null,
        catalog_id: base.catalog_id || null,
        color_ids: colorIds || [],
        isbn: base.isbn || null,
        author: base.author || null,
        book_title: base.book_title || null,
        model: base.model || null,
        video_game_rating_id: base.video_game_rating_id || null,
        measurement_length: base.measurement_length != null ? base.measurement_length : null,
        measurement_width: base.measurement_width != null ? base.measurement_width : null,
        measurement_unit: base.measurement_unit != null ? base.measurement_unit : null,
        photos: photoUrls,
      };
  if (!assigned.length) {
        throw new Error('No photos could be uploaded; aborting create to avoid 400.');
      }
  // Delete the original item BEFORE creating the clone, as requested
  btn.textContent = 'Deleting…';
  let originalDeleted = false;
  await deleteItem(csrf, itemId);
  originalDeleted = true;
  btn.textContent = 'Creating…';
  const created = await createItem(csrf, payload);
  alert('Item cloned. New ID: ' + (created && created.item && created.item.id ? created.item.id : 'unknown'));
  // Refresh the page to reflect deletion and the new item
  window.location.reload();
    } catch (e) {
      console.error(e);
      // If we already deleted the original, surface its data to the user
      try {
        // Access variables if defined in this scope
        if (typeof originalDeleted !== 'undefined' && originalDeleted === true && typeof deletedSummary !== 'undefined') {
          const text = JSON.stringify(deletedSummary, null, 2);
          // Attempt to copy to clipboard for convenience
          try { await navigator.clipboard.writeText(text); } catch (_) {}
          alert('Failed to republish after deletion. Original item data (copied to clipboard if permitted):\n' + text);
        } else {
          alert('Failed to republish: ' + (e && e.message ? e.message : e));
        }
      } catch (_) {
        alert('Failed to republish: ' + (e && e.message ? e.message : e));
      }
    } finally {
  btn.disabled = false; btn.classList.remove('is-loading'); btn.textContent = 'Republier';
    }
  }

  function injectButtons() {
  ensureStyles();
  // Fetch the wardrobe page once to annotate items without per-item calls
  fetchWardrobeAndAnnotate();
    const bumped = document.querySelectorAll(BOOST_BUTTON_SELECTOR);
    let count = 0;
    for (const b of bumped) {
      // Avoid duplicate injection
      if (b.parentElement && b.parentElement.querySelector('.vinted-republish-btn')) continue;
      const btn = makeButton('Republier');
      btn.classList.add('vinted-republish-btn');
      btn.addEventListener('click', e => {
        e.preventDefault();
        e.stopPropagation();
        republishFromButton(btn);
      });
      // Insert next to Booster button
      if (b.parentElement) {
    // Add an extra line/spacing between Booster and Republish
    const spacer = document.createElement('div');
    spacer.style.height = '8px';
    spacer.style.width = '100%';
    b.parentElement.appendChild(spacer);
    b.parentElement.appendChild(btn);
        count++;
      }
      // Try to render any known labels for visible items
      applyCreatedLabels();
    }
    if (count > 0) log('Injected', count, 'republish buttons');
    // Final pass in case new nodes appeared
    applyCreatedLabels();
  }

  // Initial and dynamic updates
  const obs = new MutationObserver(() => injectButtons());
  obs.observe(document.documentElement, { childList: true, subtree: true });
  injectButtons();
})();
