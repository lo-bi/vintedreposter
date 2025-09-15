// Service worker: capture CSRF and anon_id headers from Vinted API requests

let latestCsrf = null;
let latestAnonId = null;

function upsertTokensFromHeaders(requestHeaders) {
  if (!Array.isArray(requestHeaders)) return;
  for (const h of requestHeaders) {
    if (!h || !h.name) continue;
    const name = h.name.toLowerCase();
    if (name === 'x-csrf-token' && h.value) {
      latestCsrf = h.value;
    } else if (name === 'x-anon-id' && h.value) {
      latestAnonId = h.value;
    }
  }
  // Persist for content scripts that load later
  try {
    chrome.storage.local.set({ vinted_csrf_token: latestCsrf, vinted_anon_id: latestAnonId });
  } catch (_) {
    // ignore
  }
}

chrome.webRequest.onBeforeSendHeaders.addListener(
  details => {
    try { upsertTokensFromHeaders(details.requestHeaders || []); } catch (_) {}
  },
  { urls: [
      "https://www.vinted.fr/api/*",
      "https://www.vinted.fr/*/api/*"
    ] },
  ["requestHeaders"]
);

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'vinted:getTokens') {
    // Try storage first if memory is empty
    if (!latestCsrf) {
      chrome.storage.local.get(['vinted_csrf_token', 'vinted_anon_id'], data => {
        const csrf = data && data.vinted_csrf_token ? data.vinted_csrf_token : latestCsrf;
        const anonId = data && data.vinted_anon_id ? data.vinted_anon_id : latestAnonId;
        sendResponse({ csrf, anonId });
      });
      return true; // async response
    }
    sendResponse({ csrf: latestCsrf, anonId: latestAnonId });
    return true;
  } else if (msg && msg.type === 'vinted:fetchArrayBuffer' && msg.url) {
    // Fetch binary data (e.g., image) from allowed hosts and return as ArrayBuffer
    (async () => {
      try {
        const res = await fetch(msg.url, { credentials: 'omit' });
        if (!res.ok) {
          sendResponse({ ok: false, status: res.status, statusText: res.statusText });
          return;
        }
        const buf = await res.arrayBuffer();
        const contentType = res.headers.get('content-type') || '';
        sendResponse({ ok: true, buffer: buf, contentType });
      } catch (e) {
        sendResponse({ ok: false, error: (e && e.message) || String(e) });
      }
    })();
    return true; // async response
  }
  return false;
});
