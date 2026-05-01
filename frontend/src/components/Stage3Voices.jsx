import React, { useState, useEffect, useRef, useMemo } from 'react';
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
  const [showVoicePicker, setShowVoicePicker]   = useState(false);
  const [voicePickerExpanded, setVoicePickerExpanded] = useState(false);
  const [voiceBySpeaker, setVoiceBySpeaker]     = useState({});
  const [voicePickerOpenBySpeaker, setVoicePickerOpenBySpeaker] = useState({});
  const [voiceOptions, setVoiceOptions]         = useState([]);
  const [voicesLoading, setVoicesLoading]       = useState(true);
  const [synthesizing, setSynthesizing]         = useState(false);
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

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await axios.get(`${apiBase}/gemini/voices`);
        const list = res.data?.voices || [];
        if (!cancelled) setVoiceOptions(list);
      } catch (err) {
        console.warn('Could not load Gemini voices list.', err);
      } finally {
        if (!cancelled) setVoicesLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [apiBase]);

  const runPipeline = async () => {
    setError('');
    setPhase('translating');
    startTimer();

    try {
      // One Gemini call per block: when session_id is provided AND the
      // session audio is on disk, the backend translates and inserts audio
      // tags in a single shot using Google's speech-generation prompting
      // guide. Otherwise it falls back to text-only translation.
      const transRes = await axios.post(`${apiBase}/translate`, {
        transcript_blocks: blocks,
        target_lang: targetLang,
        source_lang: sourceLang,
        session_id: sessionId || null,
      });
      const tBlocks = transRes.data.blocks || [];

      const finalBlocks = tBlocks.map((b) => ({
        ...b,
        tagged_transcript: b.tagged_transcript || b.transcript,
        emotion_tags: b.emotion_tags || [],
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

  const uniqueSpeakers = useMemo(() => {
    const seen = new Set();
    const list = [];
    for (const b of editableBlocks) {
      const sid = b.speakers?.[0];
      if (sid && !seen.has(sid)) {
        seen.add(sid);
        list.push(sid);
      }
    }
    return list;
  }, [editableBlocks]);

  const setSpeakerVoice = (speakerId, voice) => {
    setVoiceBySpeaker((prev) => ({ ...prev, [speakerId]: voice }));
  };

  const VOICE_STYLE_HINTS = {
    Algenib: 'Gravelly baritone',
    Orus: 'Firm formal baritone',
    Charon: 'Polished neutral narrator',
    Iapetus: 'Clear articulate mid-range',
    Enceladus: 'Breathy intimate rasp',
    Fenrir: 'Excitable high-energy',
    Puck: 'Upbeat cheerful youthful',
    Umbriel: 'Easy-going laid-back',
    Algieba: 'Smooth silky',
    Schedar: 'Even-toned measured',
    Achird: 'Friendly warm',
    Zubenelgenubi: 'Casual conversational',
    Sadachbia: 'Lively animated',
    Sadaltager: 'Knowledgeable confident authority',
    Alnilam: 'Firm grounded deep',
    Rasalgethi: 'Informative articulate',
    Zephyr: 'Bright clear upbeat',
    Kore: 'Firm confident',
    Leda: 'Youthful light',
    Aoede: 'Breezy relaxed',
    Callirrhoe: 'Easy-going natural',
    Autonoe: 'Bright crisp',
    Despina: 'Smooth velvety',
    Erinome: 'Clear articulate',
    Laomedeia: 'Upbeat energetic',
    Achernar: 'Soft gentle',
    Gacrux: 'Mature rich',
    Pulcherrima: 'Bold attention-grabbing',
    Vindemiatrix: 'Gentle warm mid-range',
    Sulafat: 'Warm rich friendly',
  };

  const getVoiceLabel = (voice) => {
    if (!voice) return 'Auto (recommended)';
    const styleHint = voice.style || VOICE_STYLE_HINTS[voice.name];
    const parts = [voice.name];
    if (voice.gender) parts.push(voice.gender);
    if (styleHint) parts.push(styleHint);
    return parts.join(' · ');
  };

  const getSelectedVoiceLabel = (speakerId) => {
    const selectedId = voiceBySpeaker[speakerId] || 'auto';
    if (selectedId === 'auto') return 'Auto (recommended)';
    const selectedVoice = voiceOptions.find((v) => v.id === selectedId);
    return getVoiceLabel(selectedVoice);
  };

  const handleConfirm = async () => {
    setError('');
    setSynthesizing(true);
    try {
      const transcriptBlocks = editableBlocks.map((b) => ({
        ...b,
        transcript: b.tagged_transcript || b.transcript || '',
      }));

      const voiceMap = Object.entries(voiceBySpeaker)
        .filter(([, voice]) => voice && voice !== 'auto')
        .map(([speakerId, voiceId]) => ({ speaker_id: speakerId, voice_id: voiceId }));

      const res = await axios.post(`${apiBase}/synthesize`, {
        session_id: sessionId || null,
        transcript_blocks: transcriptBlocks,
        voice_map: voiceMap,
        target_lang: targetLang,
        auto_detect_speakers: false,
        synthesis_mode: 'batched_per_speaker_gemini',
      });

      onComplete(res.data?.audio_url, res.data?.video_url);
    } catch (err) {
      const detail = err?.response?.data?.detail || err.message || 'Unknown error';
      setError(`Synthesis failed: ${detail}`);
    } finally {
      setSynthesizing(false);
    }
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
            Parrot Translate translates to {langName(targetLang)}
          </p>
        </div>
      </div>

      {phase === 'idle' && (
        <>
          <div className="info-card">
            <Info size={16} />
            <div>
              <strong>{blocks.length} segments</strong> ready to translate from original language{' '}
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
              Translating {blocks.length} segments via Parrot Translate…
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
                      rows={Math.min(4, Math.max(2, Math.ceil((b.tagged_transcript || '').length / 70)))}
                      onChange={(e) => updateTaggedTranscript(b._key, e.target.value)}
                    />
                    <TagBadge text={b.tagged_transcript || ''} />
                  </div>
                </div>
              );
            })}
          </div>

          <div className="action-row stage3-sticky-actions">
            <button className="btn-secondary" onClick={() => { setPhase('idle'); setEditableBlocks([]); }}>
              ← Re-run
            </button>
            {!showVoicePicker ? (
              <button
                className="btn-primary"
                onClick={() => {
                  setShowVoicePicker(true);
                  setVoicePickerExpanded(false);
                }}
              >
                <CheckCircle size={18} />
                Choose Voices →
              </button>
            ) : (
              <button className="btn-primary" onClick={handleConfirm} disabled={synthesizing}>
                <CheckCircle size={18} />
                {synthesizing ? 'Synthesizing…' : 'Confirm & Continue →'}
              </button>
            )}
          </div>

          {showVoicePicker && (
            <div className="voice-picker-card">
              <button
                type="button"
                className="voice-picker-toggle"
                onClick={() => setVoicePickerExpanded((v) => !v)}
                aria-expanded={voicePickerExpanded}
              >
                <span className="voice-picker-title">Choose voice per speaker</span>
                <span className="lang-picker-caret">{voicePickerExpanded ? '▴' : '▾'}</span>
              </button>
              {voicePickerExpanded && (
                <div className="voice-picker-grid">
                  {uniqueSpeakers.map((sid) => (
                    <div key={sid} className="voice-picker-row">
                      <span>{sid}</span>
                      <div className="voice-picker-controls">
                        <button
                          type="button"
                          className="lang-picker-trigger"
                          onClick={() => setVoicePickerOpenBySpeaker((prev) => ({ ...prev, [sid]: !prev[sid] }))}
                          disabled={voicesLoading}
                          aria-expanded={!!voicePickerOpenBySpeaker[sid]}
                          aria-label={`Choose voice for ${sid}`}
                        >
                          <span className="voice-choice-label">{getSelectedVoiceLabel(sid)}</span>
                          <span className="lang-picker-caret">{voicePickerOpenBySpeaker[sid] ? '▴' : '▾'}</span>
                        </button>
                        {voicePickerOpenBySpeaker[sid] && (
                          <div className={`lang-scroll-list ${voicesLoading ? 'disabled' : ''}`} role="listbox" aria-label={`Voice options for ${sid}`}>
                            <button
                              type="button"
                              className={`lang-option ${(voiceBySpeaker[sid] || 'auto') === 'auto' ? 'selected' : ''}`}
                              onClick={() => {
                                setSpeakerVoice(sid, 'auto');
                                setVoicePickerOpenBySpeaker((prev) => ({ ...prev, [sid]: false }));
                              }}
                              disabled={voicesLoading}
                              aria-selected={(voiceBySpeaker[sid] || 'auto') === 'auto'}
                            >
                              <span>Auto (recommended)</span>
                            </button>
                            {voiceOptions.map((v) => (
                              <button
                                key={v.id}
                                type="button"
                                className={`lang-option ${voiceBySpeaker[sid] === v.id ? 'selected' : ''}`}
                                onClick={() => {
                                  setSpeakerVoice(sid, v.id);
                                  setVoicePickerOpenBySpeaker((prev) => ({ ...prev, [sid]: false }));
                                }}
                                disabled={voicesLoading}
                                aria-selected={voiceBySpeaker[sid] === v.id}
                              >
                                <span>{getVoiceLabel(v)}</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                      {voicesLoading && <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>Loading voices...</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}