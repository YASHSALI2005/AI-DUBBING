/** Heuristics match backend `app.py` defaults; `/api/config` overrides via `timing`. */

export function estimateSttSecondsFromDuration(audioDurationSec, timing) {
  const perMin = Number(timing?.stt_seconds_per_minute_audio) || 35;
  const minEst = Number(timing?.stt_min_estimate_seconds) || 20;
  if (!audioDurationSec || !Number.isFinite(audioDurationSec) || audioDurationSec <= 0) {
    return minEst;
  }
  return Math.max(minEst, (audioDurationSec / 60) * perMin);
}

export function estimateTranslateSeconds(blocks, sourceLang, targetLang, timing) {
  const w = Math.max(1, Number(timing?.translate_max_workers) || 4);
  const secPer = Number(timing?.translate_seconds_per_block) || 0.9;
  const resolved =
    !sourceLang || String(sourceLang).toLowerCase() === 'auto' ? 'hi-IN' : sourceLang;
  let n = 0;
  for (const b of blocks || []) {
    const t = (b.transcript || '').trim();
    if (!t) continue;
    if (targetLang !== resolved) n += 1;
  }
  if (n <= 0) return 2;
  const waves = Math.ceil(n / w);
  return Math.max(3, waves * secPer);
}

export function estimateEnhanceSeconds(blocks, timing) {
  const w = Math.max(1, Number(timing?.gemini_max_workers) || 1);
  const secPer = Number(timing?.gemini_seconds_per_block) || 4;
  const n = (blocks || []).filter((b) => (b.transcript || '').trim()).length;
  if (n <= 0) return 0;
  const waves = Math.ceil(n / w);
  return Math.max(5, waves * secPer);
}

export function formatEtaRange(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '~a few seconds';
  const lo = Math.max(8, Math.round(seconds * 0.55));
  const hi = Math.round(seconds * 1.4);
  if (hi < 90) return `~${lo}–${hi}s`;
  return `~${Math.ceil(lo / 60)}–${Math.ceil(hi / 60)} min`;
}

export function formatElapsedClock(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '0s';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
