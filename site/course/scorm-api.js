/* SCORM 1.2 runtime shim.
 * Discovers the LMS `API` object, wraps LMSInitialize / LMSGetValue /
 * LMSSetValue / LMSCommit / LMSFinish, and degrades to a no-op "preview
 * mode" when opened directly in a browser (file:// with no LMS parent).
 * Zero network calls. Exposes a single global: window.Scorm.
 */
(function () {
  "use strict";

  // ---- API discovery (standard 1.2 walk: parent chain, then opener) ----
  function findAPIIn(win) {
    var hops = 0;
    while (win && hops < 10) {
      if (win.API) return win.API;
      if (win.parent === win) break;
      win = win.parent;
      hops++;
    }
    return null;
  }

  function discover() {
    var api = findAPIIn(window);
    if (!api && window.opener) api = findAPIIn(window.opener);
    return api;
  }

  var API = null;
  try { API = discover(); } catch (e) { API = null; }

  var connected = false;
  var previewMode = !API;

  // ---- throttled commit (max ~1/second) ----
  var lastCommit = 0;
  var pendingCommit = null;

  function rawCommit() {
    lastCommit = Date.now();
    if (API) { try { API.LMSCommit(""); } catch (e) {} }
  }

  var Scorm = {
    previewMode: previewMode,

    init: function () {
      if (!API) { previewMode = true; this.previewMode = true; return false; }
      try {
        var ok = API.LMSInitialize("");
        connected = (ok === "true" || ok === true);
      } catch (e) { connected = false; }
      previewMode = !connected;
      this.previewMode = previewMode;
      return connected;
    },

    get: function (key) {
      if (!connected) return "";
      try { return API.LMSGetValue(key); } catch (e) { return ""; }
    },

    set: function (key, value) {
      if (!connected) return false;
      try { API.LMSSetValue(key, String(value)); return true; } catch (e) { return false; }
    },

    // Throttled: coalesces bursts into at most one commit per second.
    commit: function () {
      if (!connected) return;
      var now = Date.now();
      var since = now - lastCommit;
      if (since >= 1000) {
        rawCommit();
      } else if (!pendingCommit) {
        pendingCommit = setTimeout(function () {
          pendingCommit = null;
          rawCommit();
        }, 1000 - since);
      }
    },

    finish: function () {
      if (!connected) return;
      if (pendingCommit) { clearTimeout(pendingCommit); pendingCommit = null; }
      try { API.LMSCommit(""); } catch (e) {}
      try { API.LMSFinish(""); } catch (e) {}
      connected = false;
    }
  };

  window.Scorm = Scorm;
})();
