import React, { useState, useEffect, useRef, useMemo } from 'react';
import axios from 'axios';
import { Languages, Loader2, CheckCircle, ArrowLeft, PlayCircle, X } from 'lucide-react';
import {
  estimateTranslateSeconds,
  estimateEnhanceSeconds,
  formatEtaRange,
  formatElapsedClock,
} from '../timeEstimates';

// Candidate language list; UI will filter by backend-supported dubbing languages.
const TARGET_LANGUAGES = [
  { code: 'en-IN', name: 'English' },
  { code: 'hi-IN', name: 'Hindi' },
  { code: 'es-IN', name: 'Marathi' },
  { code: 'bn-IN', name: 'Bengali' },
  { code: 'ta-IN', name: 'Tamil' },
  { code: 'te-IN', name: 'Telugu' },
  { code: 'mr-IN', name: 'Marathi' },
  { code: 'kn-IN', name: 'Kannada' },
  { code: 'ml-IN', name: 'Malayalam' },
  { code: 'gu-IN', name: 'Gujarati' },
  { code: 'pa-IN', name: 'Punjabi' },
  { code: 'od-IN', name: 'Odia' },
];

export default function Stage2Translate({ apiBase, blocks, sourceLang, sessionId, videoFile = null, onComplete }) {
  const [targetLang, setTargetLang] = useState('hi-IN');
  const [useGeminiEnhancement, setUseGeminiEnhancement] = useState(true);
  const [loading, setLoading] = useState(false);
  const [originalBlocks, setOriginalBlocks] = useState([]);
  const [translatedBlocks, setTranslatedBlocks] = useState([]);
  const [hasTranslated, setHasTranslated] = useState(false);
  const [supportedTargetLanguages, setSupportedTargetLanguages] = useState(TARGET_LANGUAGES);
  // Default: drive dub from Stage 2 lines via Sarvam. "PARROT AI_auto" re-dubs from the file and ignores this text.
  const [experimentMode, setExperimentMode] = useState('translated_sarvam');
  const [timingCfg, setTimingCfg] = useState(null);
  const [lastRunSummary, setLastRunSummary] = useState(null);
  const loadTimingRef = useRef(null);
  const [, renderTick] = useState(0);
  /** Sarvam translate: '' = omit (model default), else Male/Female for agreement on lines like "I'm asking…". */
  const [speakerGenderById, setSpeakerGenderById] = useState({});

  const speakerIdsOrdered = useMemo(() => {
    const ordered = [];
    const seen = new Set();
    for (const b of originalBlocks || []) {
      const id = b.speakers?.[0];
      if (id && !seen.has(id)) {
        seen.add(id);
        ordered.push(id);
      }
    }
    return ordered;
  }, [originalBlocks]);

  /** First line start time (seconds) per diarized speaker — used to jump the reference clip. */
  const speakerFirstStartSec = useMemo(() => {
    const m = {};
    for (const b of originalBlocks || []) {
      const sid = b.speakers?.[0];
      if (!sid || m[sid] !== undefined) continue;
      const ts = b.timestamps;
      if (Array.isArray(ts) && ts.length > 0 && Number.isFinite(Number(ts[0]))) {
        m[sid] = Number(ts[0]);
      }
    }
    return m;
  }, [originalBlocks]);

  const [videoUrl, setVideoUrl] = useState(null);
  const [playingVideo, setPlayingVideo] = useState(false);
  const videoRef = useRef(null);
  const playTimeoutRef = useRef(null);

  useEffect(() => {
    if (!videoFile) {
      setVideoUrl(null);
      return undefined;
    }
    const url = URL.createObjectURL(videoFile);
    setVideoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [videoFile]);

  const playSpeakerReference = (speakerId) => {
    const start = speakerFirstStartSec[speakerId];
    const t = Number.isFinite(start) ? start : 0;
    if (!videoRef.current || !videoUrl) return;
    clearTimeout(playTimeoutRef.current);
    setPlayingVideo(true);
    videoRef.current.currentTime = t;
    videoRef.current.play().catch(() => {});
    playTimeoutRef.current = setTimeout(() => {
      if (videoRef.current) videoRef.current.pause();
      setPlayingVideo(false);
    }, 4000);
  };

  useEffect(() => {
    setSpeakerGenderById((prev) => {
      const next = {};
      for (const id of speakerIdsOrdered) {
        next[id] = Object.prototype.hasOwnProperty.call(prev, id) ? prev[id] : '';
      }
      return next;
    });
  }, [speakerIdsOrdered]);

  useEffect(() => {
    if (blocks) {
      setOriginalBlocks([...blocks]);
    }
  }, [blocks]);

  useEffect(() => {
    const loadSupportedLanguages = async () => {
      try {
        const res = await axios.get(`${apiBase}/config`);
        if (res.data?.timing) {
          setTimingCfg(res.data.timing);
        }
        const supportedCodes = new Set(res.data.supported_target_languages || []);
        const filtered = TARGET_LANGUAGES.filter((lang) => supportedCodes.has(lang.code));
        if (filtered.length > 0) {
          setSupportedTargetLanguages(filtered);
          if (!filtered.some((lang) => lang.code === targetLang)) {
            setTargetLang(filtered[0].code);
          }
        }
      } catch (err) {
        // If config fetch fails, keep default list to avoid blocking flow.
        console.warn("Could not load supported target languages from backend config.", err);
      }
    };

    loadSupportedLanguages();
  }, [apiBase]);

  useEffect(() => {
    if (!loading) return undefined;
    const id = setInterval(() => renderTick((n) => n + 1), 450);
    return () => clearInterval(id);
  }, [loading]);

  const handleTextChange = (index, newText, isTranslated = false) => {
    if (isTranslated) {
      const updated = [...translatedBlocks];
      updated[index].transcript = newText;
      setTranslatedBlocks(updated);
    } else {
      const updated = [...originalBlocks];
      updated[index].transcript = newText;
      setOriginalBlocks(updated);
    }
  };

  const handleTranslate = async () => {
    setLoading(true);
    const translateEst = estimateTranslateSeconds(originalBlocks, sourceLang, targetLang, timingCfg);
    const enhanceEst =
      useGeminiEnhancement && sessionId
        ? estimateEnhanceSeconds(originalBlocks, timingCfg)
        : 0;
    const totalEst = translateEst + enhanceEst;
    loadTimingRef.current = { start: Date.now(), translateEst, enhanceEst, totalEst };
    try {
      const speaker_genders = Object.fromEntries(
        Object.entries(speakerGenderById).filter(([, v]) => v === 'Male' || v === 'Female')
      );
      const res = await axios.post(`${apiBase}/translate`, {
        transcript_blocks: originalBlocks,
        target_lang: targetLang,
        source_lang: sourceLang,
        speaker_genders,
      });
      let finalBlocks = res.data.blocks;
      if (res.data.failed_block_count > 0) {
        alert(`Translation completed with ${res.data.failed_block_count} fallback block(s). You can manually edit them below.`);
      }

      let enhanceActual = null;
      let estEnhance = null;
      // Optional automatic background enhancement with Gemini after initial translation.
      if (useGeminiEnhancement && sessionId) {
        try {
          const enhanceRes = await axios.post(`${apiBase}/enhance-translation`, {
            session_id: sessionId,
            transcript_blocks: finalBlocks,
            source_lang: sourceLang,
            target_lang: targetLang,
          });
          finalBlocks = enhanceRes.data.blocks || finalBlocks;
          enhanceActual = enhanceRes.data.enhance_processing_seconds;
          estEnhance = enhanceRes.data.estimated_enhance_seconds;
          if (enhanceRes.data.failed_block_count > 0) {
            const firstFailure = (enhanceRes.data.failed_blocks || [])[0];
            const firstError = firstFailure?.error ? String(firstFailure.error).slice(0, 500) : 'Unknown Gemini error';
            alert(
              `Gemini enhancement completed with ${enhanceRes.data.failed_block_count} fallback block(s).\n\nFirst error:\n${firstError}`
            );
          }
        } catch (enhanceErr) {
          console.error(enhanceErr);
          // Keep translated text if enhancement fails.
          const detail = enhanceErr?.response?.data?.detail;
          alert(`Gemini enhancement failed, continuing with base translation.${detail ? `\n\n${detail}` : ''}`);
        }
      } else if (useGeminiEnhancement && !sessionId) {
        alert('Gemini enhancement is enabled, but session is missing. Continuing with base translation.');
      }

      const translateActual = res.data.translation_processing_seconds;
      const estT = res.data.estimated_translate_seconds;
      const parts = [];
      if (translateActual != null && estT != null) {
        parts.push(`translate ${Number(translateActual).toFixed(1)}s (estimate was ~${Number(estT).toFixed(0)}s)`);
      }
      if (enhanceActual != null && estEnhance != null) {
        parts.push(`refine ${Number(enhanceActual).toFixed(1)}s (estimate ~${Number(estEnhance).toFixed(0)}s)`);
      }
      if (parts.length) setLastRunSummary(parts.join(' · '));

      setTranslatedBlocks(finalBlocks);
      setHasTranslated(true);
    } catch (err) {
        console.error(err);
        alert("Translation failed. See console for details.");
    } finally {
      setLoading(false);
      loadTimingRef.current = null;
    }
  };

  const currentDisplayBlocks = hasTranslated ? translatedBlocks : originalBlocks;
  const sourceLangName = TARGET_LANGUAGES.find(l => l.code === sourceLang)?.name || 'Original';
  const targetLangName = supportedTargetLanguages.find(l => l.code === targetLang)?.name || 'Target';
  const normalizedDubbingTargetCode = (targetLang || '').split('-')[0].toLowerCase();
  const translateEtaPreview = estimateTranslateSeconds(originalBlocks, sourceLang, targetLang, timingCfg);
  const enhanceEtaPreview =
    useGeminiEnhancement && sessionId ? estimateEnhanceSeconds(originalBlocks, timingCfg) : 0;
  const totalEtaPreview = translateEtaPreview + enhanceEtaPreview;
  const loadProgress = loading && loadTimingRef.current
    ? {
        elapsed: (Date.now() - loadTimingRef.current.start) / 1000,
        totalEst: loadTimingRef.current.totalEst,
      }
    : null;

  return (
    <div>
      <h2 style={{fontSize: '1.5rem', marginBottom: '1rem', textAlign: 'center'}}>
        {hasTranslated ? `Review ${targetLangName} Translation` : `Select Translation Language`}
      </h2>
      
      {!hasTranslated ? (
        <p style={{color: 'var(--text-muted)', marginBottom: '2rem', textAlign: 'center'}}>
            The audio has been successfully transcribed. Please select the target language to continue.
        </p>
      ) : (
        <p style={{color: 'var(--text-muted)', marginBottom: '2rem', textAlign: 'center'}}>
            Review and refine the {targetLangName.toLowerCase()} translation below.
        </p>
      )}

      <div style={{
        margin: '0 auto 1.5rem auto',
        maxWidth: '560px',
        background: 'rgba(59, 130, 246, 0.10)',
        border: '1px solid rgba(59, 130, 246, 0.35)',
        borderRadius: '10px',
        padding: '0.75rem 1rem',
        color: '#bfdbfe',
        fontSize: '0.92rem',
        textAlign: 'center'
      }}>
        Dubbing target that will be sent to ElevenLabs: <strong>{targetLangName}</strong> (<code>{normalizedDubbingTargetCode}</code>)
      </div>

      {hasTranslated && lastRunSummary && (
        <p
          style={{
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: '0.88rem',
            margin: '-0.5rem auto 1rem auto',
            maxWidth: '560px',
          }}
        >
          Last run: {lastRunSummary}
        </p>
      )}

      {hasTranslated && (
        <div style={{ background: 'var(--bg-card)', padding: '1.5rem', borderRadius: '12px', marginBottom: '2rem', border: '1px solid var(--border-light)' }}>
          <h3 style={{marginBottom: '1rem', color: 'var(--text-muted)'}}>
              {targetLangName} Translation Results
          </h3>
          <div style={{display: 'flex', flexDirection: 'column', gap: '1rem', maxHeight: '400px', overflowY: 'auto', paddingRight: '0.5rem'}}>
            {translatedBlocks.map((b, i) => (
              <div key={i} style={{display: 'flex', gap: '1rem', alignItems: 'flex-start'}}>
                  <span style={{
                      background: 'rgba(59, 130, 246, 0.2)', 
                      color: 'var(--primary)', 
                      padding: '0.4rem 0.6rem', 
                      borderRadius: '6px', 
                      fontSize: '0.8rem',
                      fontWeight: 'bold',
                      border: '1px solid rgba(59, 130, 246, 0.4)',
                      minWidth: '40px',
                      textAlign: 'center'
                  }}>
                      {b.speakers[0] || 'S?'}
                  </span>
                  <textarea 
                    value={b.transcript}
                    onChange={(e) => handleTextChange(i, e.target.value, true)}
                    style={{
                      flex: 1, 
                      margin: 0, 
                      lineHeight: '1.5', 
                      background: 'rgba(0,0,0,0.3)', 
                      border: '1px solid var(--border-light)',
                      color: 'var(--text-light)',
                      padding: '0.75rem',
                      borderRadius: '8px',
                      resize: 'vertical',
                      minHeight: '60px'
                    }}
                  />
              </div>
            ))}
          </div>
        </div>
      )}

      {!hasTranslated ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1.5rem' }}>
            <div className="form-group" style={{width: '100%', maxWidth: '400px'}}>
                <label>Translate Video To:</label>
                <select value={targetLang} onChange={(e) => setTargetLang(e.target.value)}>
                {supportedTargetLanguages.map(l => (
                    <option key={l.code} value={l.code}>{l.name}</option>
                ))}
                </select>
            </div>
            {speakerIdsOrdered.length > 0 && (
              <div
                style={{
                  width: '100%',
                  maxWidth: '440px',
                  padding: '0.75rem 1rem',
                  borderRadius: '10px',
                  border: '1px solid var(--border-light)',
                  background: 'rgba(0,0,0,0.2)',
                  textAlign: 'left',
                }}
              >
                <p style={{ margin: '0 0 0.5rem 0', fontSize: '0.88rem', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                  <strong>Speaker gender</strong> (optional): English often does not mark if &quot;I&quot; is male or
                  female; Hindi needs it (e.g. पूछ <em>रहा</em> vs पूछ <em>रही</em>). Set below so Sarvam translate can
                  match the scene — leave Auto if unsure.
                </p>
                <p style={{ margin: '0 0 0.65rem 0', fontSize: '0.82rem', color: 'var(--text-muted)', lineHeight: 1.35 }}>
                  <strong>Reference</strong> plays ~4s of your video from that speaker&apos;s <em>first</em> line in the
                  transcript (same as Stage 3) so you can tell who is S1 vs S2.
                </p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  {speakerIdsOrdered.map((sid) => (
                    <div key={sid} style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
                      <span style={{ minWidth: '2.5rem', fontWeight: 600, color: 'var(--text-light)' }}>{sid}</span>
                      <select
                        value={speakerGenderById[sid] ?? ''}
                        onChange={(e) =>
                          setSpeakerGenderById((prev) => ({ ...prev, [sid]: e.target.value }))
                        }
                        style={{
                          flex: 1,
                          minWidth: '120px',
                          background: 'rgba(15, 23, 42, 0.8)',
                          border: '1px solid var(--border-light)',
                          color: 'white',
                          padding: '0.35rem 0.5rem',
                          borderRadius: '6px',
                        }}
                      >
                        <option value="">Auto</option>
                        <option value="Male">Male</option>
                        <option value="Female">Female</option>
                      </select>
                      <button
                        type="button"
                        disabled={!videoUrl}
                        onClick={() => playSpeakerReference(sid)}
                        title={
                          videoUrl
                            ? `Play video from ${(speakerFirstStartSec[sid] ?? 0).toFixed(2)}s (first line for ${sid})`
                            : 'Video file not available (e.g. after reload — re-upload from Stage 1)'
                        }
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '0.25rem',
                          padding: '0.35rem 0.55rem',
                          borderRadius: '6px',
                          border: '1px solid rgba(59, 130, 246, 0.35)',
                          background: videoUrl ? 'rgba(59, 130, 246, 0.12)' : 'rgba(30,41,59,0.5)',
                          color: videoUrl ? '#93c5fd' : 'var(--text-muted)',
                          cursor: videoUrl ? 'pointer' : 'not-allowed',
                          fontSize: '0.8rem',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        <PlayCircle size={14} /> Reference
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div style={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
              <label
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '0.6rem',
                  color: 'var(--text-muted)',
                  textAlign: 'left',
                  lineHeight: 1.4
                }}
              >
                <input
                  type="checkbox"
                  checked={useGeminiEnhancement}
                  onChange={(e) => setUseGeminiEnhancement(e.target.checked)}
                  style={{ marginTop: '0.2rem', flexShrink: 0 }}
                />
                <span style={{ maxWidth: '320px' }}>
                  Use Gemini 2.5 Flash refinement in background (emotion/tone aware)
                </span>
              </label>
            </div>

            {!loading && (
              <p
                style={{
                  color: 'var(--text-muted)',
                  fontSize: '0.9rem',
                  textAlign: 'center',
                  maxWidth: '480px',
                  margin: 0,
                }}
              >
                Rough backend wall time (parallel translate
                {useGeminiEnhancement && sessionId ? ' + Gemini refine' : ''}):{' '}
                <strong>{formatEtaRange(totalEtaPreview)}</strong>
                {' · '}
                {originalBlocks.length} line{originalBlocks.length === 1 ? '' : 's'}
              </p>
            )}

            <button className="btn" onClick={handleTranslate} disabled={loading} style={{ background: 'var(--accent)', minWidth: '250px' }}>
                {loading ? (
                    <><Loader2 className="loader" size={20} /> {useGeminiEnhancement ? 'Translating + Enhancing...' : 'Translating...'}</>
                ) : (
                    <><Languages size={20} /> Translate to {supportedTargetLanguages.find(l => l.code === targetLang)?.name}</>
                )}
            </button>
            {loadProgress && (
              <p
                style={{
                  color: 'var(--text-muted)',
                  fontSize: '0.92rem',
                  textAlign: 'center',
                  maxWidth: '440px',
                }}
              >
                Estimated total <strong>{formatEtaRange(loadProgress.totalEst)}</strong>
                {' · '}
                Elapsed <strong>{formatElapsedClock(loadProgress.elapsed)}</strong>
              </p>
            )}
        </div>
      ) : (
        <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center' }}>
            <button
              className="btn btn-secondary"
              onClick={() => {
                setHasTranslated(false);
                setLastRunSummary(null);
              }}
            >
                <ArrowLeft size={20} /> Change Language
            </button>
            <div className="form-group" style={{ minWidth: '340px', maxWidth: '520px', margin: '0 auto' }}>
              <label>How Stage 3 should dub</label>
              <select value={experimentMode} onChange={(e) => setExperimentMode(e.target.value)}>
                <option value="translated_sarvam">Sarvam: speak the translation you reviewed above (recommended)</option>
                <option value="elevenlabs_auto">ElevenLabs: auto-dub from original video file (ignores translation text)</option>
                <option value="gemini_segment_dub">Gemini: per-segment single-speaker TTS (supports 3+ speakers, ignores translation text)</option>
                <option value="hindi_transcribed">Sarvam: speak Stage 1 raw STT only (not your Stage 2 translation)</option>
                <option value="hindi_romanized">Sarvam: same as translation above (edit Roman Hindi in the list if needed)</option>
                <option value="english_transcribed">Sarvam: lines above (e.g. after translate-to-English)</option>
                <option value="without_transcribed">Sarvam: blank lines (timing only)</option>
              </select>
              {experimentMode === 'hindi_transcribed' && (
                <p
                  style={{
                    marginTop: '0.65rem',
                    fontSize: '0.86rem',
                    lineHeight: 1.45,
                    color: '#fca5a5',
                    textAlign: 'left',
                  }}
                >
                  This mode <strong>drops your Hindi translation</strong> and sends the <strong>original transcript</strong>{' '}
                  from upload (English if the video was English). That is why dubbing can sound English even after you
                  translated in Stage 2. Use &quot;Sarvam: speak the translation you reviewed above&quot; instead.
                </p>
              )}
              {experimentMode === 'hindi_romanized' && (
                <p style={{ marginTop: '0.5rem', fontSize: '0.86rem', color: 'var(--text-muted)', textAlign: 'left' }}>
                  Uses the same blocks as the review list (your translated lines). The label is legacy; Devanagari Hindi
                  is fine here.
                </p>
              )}
            </div>
            <button
              className="btn"
              onClick={() => {
                let blocksForExperiment = translatedBlocks;
                if (experimentMode === 'hindi_transcribed') {
                  blocksForExperiment = originalBlocks;
                } else if (experimentMode === 'without_transcribed') {
                  blocksForExperiment = translatedBlocks.map((b) => ({ ...b, transcript: '' }));
                }
                onComplete(blocksForExperiment, targetLang, experimentMode);
              }}
              style={{ background: 'var(--primary)' }}
            >
                <CheckCircle size={20} /> Confirm & Proceed to Voices
            </button>
        </div>
      )}

      {videoUrl && (
        <div
          style={{
            display: playingVideo ? 'block' : 'none',
            position: 'fixed',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            width: '400px',
            maxWidth: '92vw',
            background: 'var(--bg-dark)',
            border: '1px solid var(--border-light)',
            borderRadius: '12px',
            padding: '1rem',
            boxShadow: '0 10px 50px rgba(0,0,0,0.8)',
            zIndex: 100,
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: '0.75rem',
            }}
          >
            <h4 style={{ margin: 0, fontSize: '0.9rem', color: 'var(--text-muted)' }}>Speaker reference</h4>
            <button
              type="button"
              onClick={() => {
                if (videoRef.current) videoRef.current.pause();
                setPlayingVideo(false);
                clearTimeout(playTimeoutRef.current);
              }}
              style={{ background: 'transparent', border: 'none', color: 'white', cursor: 'pointer' }}
            >
              <X size={16} />
            </button>
          </div>
          <video
            ref={videoRef}
            src={videoUrl}
            style={{ width: '100%', borderRadius: '8px', background: 'black' }}
            controls
          />
        </div>
      )}
    </div>
  );
}
