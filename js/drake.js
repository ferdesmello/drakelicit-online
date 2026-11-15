/*
 * Drakelicit online — shared browser code (no dependencies).
 *
 *   Drake.individualN(answersById, questions)  -> N distribution of one person (Monte Carlo + KDE)
 *   Drake.buildPayload(...) / Drake.submit(payload)  -> what is sent to the Google Form
 *   Drake.encodeResult / decodeResult          -> bookmarkable results link (#r=...)
 *
 * The maths is the same as scripts/compute.py (truncated split-normal in T-space,
 * T = log10 of the parameter), so the browser and the monthly Action agree.
 */
(function (root) {
  'use strict';

  var Z90 = 1.2816, SIG_MIN = 0.02, N_MC = 20000, N_GRID = 600;
  var PAYLOAD_VERSION = 1;

  /* ── normal distribution ─────────────────────────────────────────────── */

  /* Φ(x), Abramowitz & Stegun 26.2.17 (abs. error < 7.5e-8) */
  function normCDF(x) {
    if (x > 8) return 1;
    if (x < -8) return 0;
    var t = 1 / (1 + 0.2316419 * Math.abs(x));
    var p = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
    var c = 1 - Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI) * p;
    return x >= 0 ? c : 1 - c;
  }

  /* Φ⁻¹(p), Acklam's algorithm (relative error < 1.2e-9) */
  var A_ = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00];
  var B_ = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01];
  var C_ = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00];
  var D_ = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00];
  function normInv(p) {
    if (p <= 0) return -Infinity;
    if (p >= 1) return Infinity;
    var q, r;
    if (p < 0.02425) {
      q = Math.sqrt(-2 * Math.log(p));
      return (((((C_[0] * q + C_[1]) * q + C_[2]) * q + C_[3]) * q + C_[4]) * q + C_[5]) /
             ((((D_[0] * q + D_[1]) * q + D_[2]) * q + D_[3]) * q + 1);
    }
    if (p > 1 - 0.02425) {
      q = Math.sqrt(-2 * Math.log(1 - p));
      return -(((((C_[0] * q + C_[1]) * q + C_[2]) * q + C_[3]) * q + C_[4]) * q + C_[5]) /
              ((((D_[0] * q + D_[1]) * q + D_[2]) * q + D_[3]) * q + 1);
    }
    q = p - 0.5; r = q * q;
    return (((((A_[0] * r + A_[1]) * r + A_[2]) * r + A_[3]) * r + A_[4]) * r + A_[5]) * q /
           (((((B_[0] * r + B_[1]) * r + B_[2]) * r + B_[3]) * r + B_[4]) * r + 1);
  }

  /* small, seedable PRNG (mulberry32); without a seed, Math.random is used */
  function makeRng(seed) {
    if (seed === undefined || seed === null) return Math.random;
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ── truncated split-normal sampler (same method as compute.py) ──────── */

  function sampleTruncated(tMode, tLo, tHi, tMin, tMax, n, rng) {
    var sL = Math.max(SIG_MIN, (tMode - tLo) / Z90);
    var sR = Math.max(SIG_MIN, (tHi - tMode) / Z90);
    var pL = normCDF((tMin - tMode) / sL);
    var pR = normCDF((tMax - tMode) / sR);
    var mL = 0.5 - pL, mR = pR - 0.5;
    var wL = sL * mL, wR = sR * mR;
    var out = new Float64Array(n);
    if (!(wL + wR > 0)) { out.fill(tMode); return out; }
    var pLeft = wL / (wL + wR);
    for (var i = 0; i < n; i++) {
      var t;
      if (rng() < pLeft) t = tMode + sL * normInv(pL + rng() * mL);
      else               t = tMode + sR * normInv(0.5 + rng() * mR);
      out[i] = t < tMin ? tMin : (t > tMax ? tMax : t);
    }
    return out;
  }

  /* ── summary: Gaussian KDE (Scott's rule) + percentiles ──────────────── */

  function percentileSorted(sorted, p) {              /* numpy 'linear' */
    var idx = (sorted.length - 1) * p / 100;
    var lo = Math.floor(idx), hi = Math.ceil(idx);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
  }

  function kdeOnGrid(samples, grid) {
    var n = samples.length, i, mean = 0, v = 0, mn = Infinity, mx = -Infinity;
    for (i = 0; i < n; i++) { mean += samples[i]; if (samples[i] < mn) mn = samples[i]; if (samples[i] > mx) mx = samples[i]; }
    mean /= n;
    for (i = 0; i < n; i++) v += (samples[i] - mean) * (samples[i] - mean);
    var h = Math.sqrt(v / (n - 1)) * Math.pow(n, -0.2);
    if (!(h > 1e-6)) h = 1e-3;
    /* bin the samples first (2000 bins), so the cost is bins x grid, not n x grid */
    var NB = 2000, width = (mx - mn) / NB || 1, counts = new Float64Array(NB);
    for (i = 0; i < n; i++) {
      var b = Math.floor((samples[i] - mn) / width);
      counts[b >= NB ? NB - 1 : b]++;
    }
    var pdf = new Float64Array(grid.length), norm = 1 / (n * h * Math.sqrt(2 * Math.PI));
    for (var g = 0; g < grid.length; g++) {
      var s = 0;
      for (var k = 0; k < NB; k++) {
        if (counts[k] === 0) continue;
        var z = (grid[g] - (mn + (k + 0.5) * width)) / h;
        if (z > 8 || z < -8) continue;
        s += counts[k] * Math.exp(-0.5 * z * z);
      }
      pdf[g] = s * norm;
    }
    return pdf;
  }

  function summariseLogN(logN) {
    var vals = [];
    for (var i = 0; i < logN.length; i++) if (isFinite(logN[i])) vals.push(logN[i]);
    if (!vals.length) return { error: 'All N samples were non-finite.' };
    var samples = Float64Array.from(vals);
    var sorted = Float64Array.from(samples).sort();
    var lo = percentileSorted(sorted, 0.5) - 0.5, hi = percentileSorted(sorted, 99.5) + 0.5;
    var grid = new Float64Array(N_GRID);
    for (var j = 0; j < N_GRID; j++) grid[j] = lo + (hi - lo) * j / (N_GRID - 1);
    var pdf = kdeOnGrid(samples, grid), area = 0;
    for (var m = 0; m < N_GRID - 1; m++) area += 0.5 * (pdf[m] + pdf[m + 1]) * (grid[m + 1] - grid[m]);
    if (area > 0) for (var q = 0; q < N_GRID; q++) pdf[q] /= area;
    var pc = function (p) { return percentileSorted(sorted, p); };
    return {
      log_N_grid: Array.from(grid), pdf: Array.from(pdf),
      log10_median: pc(50), log10_q05: pc(5), log10_q25: pc(25), log10_q75: pc(75), log10_q95: pc(95),
      N_median: Math.pow(10, pc(50)), N_q05: Math.pow(10, pc(5)), N_q25: Math.pow(10, pc(25)),
      N_q75: Math.pow(10, pc(75)), N_q95: Math.pow(10, pc(95))
    };
  }

  /*
   * answers: { [question_id]: {t_lo, t_mode, t_hi, skipped, axis_mode} }
   * questions: the array from questions.json
   */
  function individualN(answers, questions, nMC, seed) {
    nMC = nMC || N_MC;
    var rng = makeRng(seed), logN = new Float64Array(nMC);
    for (var qi = 0; qi < questions.length; qi++) {
      var Q = questions[qi], a = answers[Q.id];
      if (!a || a.skipped) return { error: "Question '" + Q.id + "' was skipped — N cannot be computed." };
      var t = sampleTruncated(a.t_mode, a.t_lo, a.t_hi, Q.tMin, Q.tMax, nMC, rng);
      var mode = a.axis_mode || Q.axisMode || 'log';
      for (var i = 0; i < nMC; i++) logN[i] += (mode === 'log') ? t[i] : Math.log10(Math.max(t[i], 1e-300));
    }
    return summariseLogN(logN);
  }

  /* ── identifiers, storage, payload ───────────────────────────────────── */

  function newUUID() {
    var c = root.crypto;
    if (c && c.randomUUID) return c.randomUUID();
    var b = c.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
    return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
  }

  var KEY_PARTICIPATED = 'drakeo_participated';   /* set after a successful send; survives "redo" */
  var KEY_LAST = 'drakeo_last';                    /* last result on this device */

  function hasParticipated() { try { return !!root.localStorage.getItem(KEY_PARTICIPATED); } catch (e) { return false; } }
  function markParticipated() { try { root.localStorage.setItem(KEY_PARTICIPATED, new Date().toISOString().slice(0, 10)); } catch (e) {} }
  function saveLast(payload, sent) { try { root.localStorage.setItem(KEY_LAST, JSON.stringify({ payload: payload, sent: sent })); } catch (e) {} }
  function loadLast() { try { return JSON.parse(root.localStorage.getItem(KEY_LAST)); } catch (e) { return null; } }

  /*
   * The object that goes to the Google Form (and, after validation by the Action,
   * into data/part-NNNN.jsonl). The date is the browser's UTC date, date only.
   * answers: [{question_id, axis_mode, t_lo, t_mode, t_hi, skipped}] in questions.json order
   * context: the object saved by context.html (or null)
   */
  function buildPayload(o) {
    var c = o.context || {}, ctx = {
      country: c.country || null,
      area_grande: c.area_grande || null,
      familiarity_astro: c.familiarity_astro || null,
      familiarity_bio: c.familiarity_bio || null,
      returning_self: c.returning_self || null,
      returning_device: hasParticipated(),
      beliefs: c.beliefs || {}
    };
    return {
      v: PAYLOAD_VERSION, id: o.id, date: new Date().toISOString().slice(0, 10),
      lang: o.lang, consent: o.consent, answers: o.answers, context: ctx
    };
  }

  /* resolves when the request left the browser; rejects on network failure / timeout.
     (Google answers with an opaque response, so a rejection by Google itself cannot be seen.) */
  function submit(payload) {
    var cfg = root.DRAKE_CONFIG || {};
    if (!cfg.FORM_URL || !cfg.FORM_ENTRY) {
      console.warn('[drake] FORM_URL / FORM_ENTRY not set in config.js: dev mode, nothing was sent.');
      return Promise.resolve({ sent: false, dev: true });
    }
    var body = new URLSearchParams();
    body.set(cfg.FORM_ENTRY, JSON.stringify(payload));
    var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, cfg.SEND_TIMEOUT_MS || 15000);
    return fetch(cfg.FORM_URL, { method: 'POST', mode: 'no-cors', body: body, signal: ctl.signal })
      .then(function () { clearTimeout(timer); return { sent: true }; })
      .catch(function (e) { clearTimeout(timer); throw e; });
  }

  /* ── bookmarkable results link: #r=<base64url of {"Ns":[lo,mode,hi] | null, ...}> ── */

  function b64urlEncode(s) { return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }
  function b64urlDecode(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    return atob(s);
  }
  function r4(x) { return Math.round(x * 10000) / 10000; }

  function encodeResult(answersArray) {
    var o = {};
    answersArray.forEach(function (a) { o[a.question_id] = a.skipped ? null : [r4(a.t_lo), r4(a.t_mode), r4(a.t_hi)]; });
    return b64urlEncode(JSON.stringify(o));
  }
  function decodeResult(str, questions) {
    try {
      var o = JSON.parse(b64urlDecode(str)), out = {};
      for (var i = 0; i < questions.length; i++) {
        var Q = questions[i], v = o[Q.id];
        if (v === null || v === undefined) { out[Q.id] = { skipped: true, axis_mode: Q.axisMode }; continue; }
        if (!Array.isArray(v) || v.length !== 3 || !v.every(function (x) { return typeof x === 'number' && isFinite(x); })) return null;
        if (!(Q.tMin - 1e-6 <= v[0] && v[0] <= v[1] && v[1] <= v[2] && v[2] <= Q.tMax + 1e-6)) return null;
        out[Q.id] = { t_lo: v[0], t_mode: v[1], t_hi: v[2], skipped: false, axis_mode: Q.axisMode };
      }
      return out;
    } catch (e) { return null; }
  }
  /* payload.answers (array) -> { [question_id]: answer } */
  function answersById(answersArray) {
    var m = {};
    answersArray.forEach(function (a) { m[a.question_id] = a; });
    return m;
  }

  root.Drake = {
    normCDF: normCDF, normInv: normInv, makeRng: makeRng,
    sampleTruncated: sampleTruncated, summariseLogN: summariseLogN, individualN: individualN,
    newUUID: newUUID, hasParticipated: hasParticipated, markParticipated: markParticipated,
    saveLast: saveLast, loadLast: loadLast,
    buildPayload: buildPayload, submit: submit,
    encodeResult: encodeResult, decodeResult: decodeResult, answersById: answersById
  };
})(typeof window !== 'undefined' ? window : globalThis);
