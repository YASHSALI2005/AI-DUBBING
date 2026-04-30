import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { Languages, Loader2, CheckCircle, Edit3, Info } from 'lucide-react';

function fmtTime(s) {
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}` : `${Number(s).toFixed(1)}s`;
}

function TagBadge({ text }) {
  const tags = [...text.matchAll(/\[([^\]]+)\]/g)].map((m) => m[1]);
  if (!tags.length) return null;
  return (
    <div className="tag-badge-row">
      {tags.map((t, i) => <span key={i} className="tag-badge">{t}</span>)}
    </div>
  );
}

export default function Stage3Translate({
  apiBase,
  blocks,            // from Stage 2 (named, reviewed)
  sourceLang,
  targetLang,
  sessionId,
  onComplete,
}) {
  const [phase, setPhase] = useState('idle'); // idle | translating | done
  const [translatedBlocks, setTranslatedBlocks] = useState([]);
  const [editableBlocks, setEditableBlocks]     = useState([]);
  const [error, setError]       = useState('');
  const [elapsed, setElapsed]   = useState(0);
  const timerRef   = useRef(null);
  const startRef   = useRef(null);

  const startTimer = () => {
    startRef.current = Date.now();
    timerRef.current = setInterval(() => setElapsed(Math.floor((Date.now() - startRef.current) / 1000)), 500);
  };
  const stopTimer = () => { clearInterval(timerRef.current); };

  useEffect(() => () => clearInterval(timerRef.current), []);

  const runPipeline = async () => {
    setError('');
    setPhase('translating');
    startTimer();

    try {
      const transRes = await axios.post(`${apiBase}/translate`, {
        transcript_blocks: blocks,
        target_lang: targetLang,
        source_lang: sourceLang,
      });
      const tBlocks = transRes.data.blocks || [];
      const finalBlocks = tBlocks.map((b) => ({
        ...b,
        tagged_transcript: b.transcript,
        emotion_tags: [],
      }));
      setTranslatedBlocks(finalBlocks);
      setEditableBlocks(finalBlocks.map((b, i) => ({ ...b, _key: i })));
      setPhase('done');
      stopTimer();
    } catch (err) {
      stopTimer();
      setPhase('idle');
      const detail = err?.response?.data?.detail || err.message || 'Unknown error';
      setError(`Translation failed: ${detail}`);
    }
  };

  const updateTaggedTranscript = (key, value) => {
    setEditableBlocks((prev) => prev.map((b) => b._key === key ? { ...b, tagged_transcript: value } : b));
  };

  const handleConfirm = () => {
    onComplete(editableBlocks, targetLang);
  };

  const langName = (code) => {
    const map = {
      hi: 'Hindi', en: 'English', es: 'Spanish', fr: 'French', de: 'German',
      ja: 'Japanese', zh: 'Chinese', ar: 'Arabic', pt: 'Portuguese', it: 'Italian',
      ko: 'Korean', ta: 'Tamil', te: 'Telugu', bn: 'Bengali', mr: 'Marathi',
      gu: 'Gujarati', kn: 'Kannada', ml: 'Malayalam', pa: 'Punjabi',
    };
    return map[code] || code;
  };

  return (
    <div className="stage-container">
      <div className="stage-header">
        <span className="stage-badge">3</span>
        <div>
          <h2 className="stage-title">Translate</h2>
          <p className="stage-subtitle">
            ElevenLabs translates to {langName(targetLang)}
          </p>
        </div>
      </div>

      {phase === 'idle' && (
        <>
          <div className="info-card">
            <Info size={16} />
            <div>
              <strong>{blocks.length} segments</strong> ready to translate from{' '}
              <strong>{langName(sourceLang)}</strong> → <strong>{langName(targetLang)}</strong>
            </div>
          </div>

          {error && <div className="error-banner">{error}</div>}

          <button className="btn-primary" onClick={runPipeline}>
            <Languages size={18} />
            Translate to {langName(targetLang)}
          </button>
        </>
      )}

      {phase === 'translating' && (
        <div className="processing-card">
          <Loader2 size={24} className="spin" />
          <div>
            <p className="proc-title">
              Translating {blocks.length} segments via ElevenLabs…
            </p>
          </div>
        </div>
      )}

      {phase === 'done' && editableBlocks.length > 0 && (
        <>
          <div className="result-header">
            <CheckCircle size={16} color="var(--color-success)" />
            <span>Translation complete — review &amp; edit below</span>
          </div>

          {/* Two-column comparison */}
          <div className="compare-grid">
            <div className="compare-col-header">Original transcript</div>
            <div className="compare-col-header">
              Translated ({langName(targetLang)}) <Edit3 size={12} style={{ opacity: 0.6 }} />
            </div>
          </div>

          <div className="translated-table">
            {editableBlocks.map((b, idx) => {
              const original = blocks[idx];
              const sid = original?.speakers?.[0] || b.speakers?.[0] || 'S?';
              const t0  = Number(b.timestamps?.[0] || 0);
              const t1  = Number(b.timestamps?.[1] || 0);
              return (
                <div key={b._key} className="tt2-row">
                  <div className="tt2-meta">
                    <span className="tt-speaker">{sid}</span>
                    <span className="tt-time">{fmtTime(t0)}–{fmtTime(t1)}</span>
                  </div>
                  <div className="tt2-original">{original?.transcript || b.original_transcript || '—'}</div>
                  <div className="tt2-translated">
                    <textarea
                      className="tt-text tagged"
                      value={b.tagged_transcript || b.transcript || ''}
                      rows={Math.max(2, Math.ceil((b.tagged_transcript || '').length / 55))}
                      onChange={(e) => updateTaggedTranscript(b._key, e.target.value)}
                    />
                    <TagBadge text={b.tagged_transcript || ''} />
                  </div>
                </div>
              );
            })}
          </div>

          <div className="action-row">
            <button className="btn-secondary" onClick={() => { setPhase('idle'); setEditableBlocks([]); }}>
              ← Re-run
            </button>
            <button className="btn-primary" onClick={handleConfirm}>
              <CheckCircle size={18} />
              Confirm &amp; Pick Voices →
            </button>
          </div>
        </>
      )}
    </div>
  );
}