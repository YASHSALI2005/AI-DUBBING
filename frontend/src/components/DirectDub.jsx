import React, { useState, useRef, useEffect, useCallback } from 'react';
import axios from 'axios';
import { Upload, Zap, CheckCircle, Download, Film, RefreshCw, Loader2, X, Globe2, Mic2, AlertTriangle } from 'lucide-react';

const LANGUAGES = [
  { code: 'hi',  name: 'Hindi',       flag: '🇮🇳' },
  { code: 'en',  name: 'English',     flag: '🇬🇧' },
  { code: 'es',  name: 'Spanish',     flag: '🇪🇸' },
  { code: 'fr',  name: 'French',      flag: '🇫🇷' },
  { code: 'de',  name: 'German',      flag: '🇩🇪' },
  { code: 'ja',  name: 'Japanese',    flag: '🇯🇵' },
  { code: 'zh',  name: 'Chinese',     flag: '🇨🇳' },
  { code: 'ar',  name: 'Arabic',      flag: '🇸🇦' },
  { code: 'pt',  name: 'Portuguese',  flag: '🇧🇷' },
  { code: 'it',  name: 'Italian',     flag: '🇮🇹' },
  { code: 'ko',  name: 'Korean',      flag: '🇰🇷' },
  { code: 'nl',  name: 'Dutch',       flag: '🇳🇱' },
  { code: 'pl',  name: 'Polish',      flag: '🇵🇱' },
  { code: 'ru',  name: 'Russian',     flag: '🇷🇺' },
  { code: 'tr',  name: 'Turkish',     flag: '🇹🇷' },
  { code: 'sv',  name: 'Swedish',     flag: '🇸🇪' },
  { code: 'ta',  name: 'Tamil',       flag: '🇮🇳' },
  { code: 'te',  name: 'Telugu',      flag: '🇮🇳' },
  { code: 'id',  name: 'Indonesian',  flag: '🇮🇩' },
  { code: 'ms',  name: 'Malay',       flag: '🇲🇾' },
  { code: 'uk',  name: 'Ukrainian',   flag: '🇺🇦' },
  { code: 'el',  name: 'Greek',       flag: '🇬🇷' },
  { code: 'vi',  name: 'Vietnamese',  flag: '🇻🇳' },
  { code: 'fil', name: 'Filipino',    flag: '🇵🇭' },
  { code: 'ro',  name: 'Romanian',    flag: '🇷🇴' },
  { code: 'hu',  name: 'Hungarian',   flag: '🇭🇺' },
  { code: 'cs',  name: 'Czech',       flag: '🇨🇿' },
  { code: 'da',  name: 'Danish',      flag: '🇩🇰' },
  { code: 'fi',  name: 'Finnish',     flag: '🇫🇮' },
  { code: 'no',  name: 'Norwegian',   flag: '🇳🇴' },
  { code: 'sk',  name: 'Slovak',      flag: '🇸🇰' },
  { code: 'bg',  name: 'Bulgarian',   flag: '🇧🇬' },
];

const ACCEPTED_TYPES = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.mp3', '.wav', '.m4a'];
const POLL_INTERVAL_MS = 5000;

function formatElapsed(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export default function DirectDub({ apiBase }) {
  const [phase, setPhase] = useState('upload');
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [targetLang, setTargetLang] = useState('hi');
  const [numSpeakers, setNumSpeakers] = useState(0);
  const [disableCloning, setDisableCloning] = useState(false);
  const [statusText, setStatusText] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [errorMsg, setErrorMsg] = useState('');
  const [finalAudioUrl, setFinalAudioUrl] = useState(null);
  const [finalVideoUrl, setFinalVideoUrl] = useState(null);
  const [dubbingId, setDubbingId] = useState(null);

  const pollRef = useRef(null);
  const elapsedRef = useRef(null);
  const fileInputRef = useRef(null);
  const sessionRef = useRef(null);
  const dubbingIdRef = useRef(null);
  const targetLangRef = useRef(targetLang);

  useEffect(() => { targetLangRef.current = targetLang; }, [targetLang]);

  useEffect(() => {
    if (phase === 'processing') {
      setElapsed(0);
      elapsedRef.current = setInterval(() => setElapsed(e => e + 1), 1000);
    } else {
      clearInterval(elapsedRef.current);
    }
    return () => clearInterval(elapsedRef.current);
  }, [phase]);

  const stopPoll = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const finalize = useCallback(async (sid, did, lang) => {
    setStatusText('Downloading dubbed content…');
    try {
      const res = await axios.post(`${apiBase}/dub-direct/finalize`, {
        session_id: sid, dubbing_id: did, target_lang: lang,
      });
      const base = 'http://localhost:8000';
      setFinalAudioUrl(res.data.audio_url ? `${base}${res.data.audio_url}` : null);
      setFinalVideoUrl(res.data.video_url ? `${base}${res.data.video_url}` : null);
      setPhase('complete');
    } catch (err) {
      const detail = err?.response?.data?.detail || err.message;
      setErrorMsg(`Download failed: ${detail}`);
      setPhase('error');
    }
  }, [apiBase]);

  const startPolling = useCallback((sid, did, lang) => {
    pollRef.current = setInterval(async () => {
      try {
        const res = await axios.get(`${apiBase}/dub-direct/status/${did}`);
        const status = (res.data.status || '').toLowerCase();
        if (status === 'dubbed') {
          stopPoll();
          await finalize(sid, did, lang);
        } else if (status === 'failed' || status === 'error') {
          stopPoll();
          setErrorMsg(`ElevenLabs dubbing failed (status: ${status}). Check your plan supports this language.`);
          setPhase('error');
        } else {
          setStatusText(`ElevenLabs processing… (${status || 'dubbing'})`);
        }
      } catch (pollErr) {
        console.warn('[DirectDub] Poll error (transient, retrying):', pollErr.message);
      }
    }, POLL_INTERVAL_MS);
  }, [apiBase, stopPoll, finalize]);

  const handleStart = async () => {
    if (!file) { alert('Please select a video or audio file.'); return; }
    setPhase('processing');
    setStatusText('Uploading to ElevenLabs…');
    setErrorMsg('');
    stopPoll();

    const form = new FormData();
    form.append('file', file);
    form.append('target_lang', targetLangRef.current);
    form.append('source_lang', 'auto');
    form.append('num_speakers', String(numSpeakers));
    form.append('disable_voice_cloning', String(disableCloning));

    try {
      const res = await axios.post(`${apiBase}/dub-direct/start`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 300_000,
      });
      const { session_id, dubbing_id } = res.data;
      sessionRef.current = session_id;
      dubbingIdRef.current = dubbing_id;
      setDubbingId(dubbing_id);
      setStatusText('ElevenLabs is dubbing your video…');
      startPolling(session_id, dubbing_id, targetLangRef.current);
    } catch (err) {
      console.error('[DirectDub] Start Error:', err);
      let detail = err?.response?.data?.detail || err.message;
      
      // If detail is a stringified JSON (common from backend proxying ElevenLabs), try to extract just the message
      if (typeof detail === 'string' && detail.includes('ElevenLabs dubbing create failed:')) {
        try {
          const jsonStr = detail.replace('ElevenLabs dubbing create failed:', '').trim();
          const parsed = JSON.parse(jsonStr);
          if (parsed.detail?.message) detail = parsed.detail.message;
          else if (parsed.message) detail = parsed.message;
        } catch (e) {
          // Fallback to original string if parsing fails
        }
      }
      
      setErrorMsg(detail);
      setPhase('error');
    }
  };

  const handleReset = () => {
    stopPoll();
    setPhase('upload');
    setFile(null);
    setErrorMsg('');
    setFinalAudioUrl(null);
    setFinalVideoUrl(null);
    setDubbingId(null);
    sessionRef.current = null;
    dubbingIdRef.current = null;
  };

  const handleFileDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) setFile(f);
  };

  const langName = LANGUAGES.find(l => l.code === targetLang)?.name || targetLang;
  const langFlag = LANGUAGES.find(l => l.code === targetLang)?.flag || '🌐';

  /* ─── UPLOAD PHASE ─── */
  if (phase === 'upload') return (
    <div style={{ maxWidth: 680, margin: '0 auto' }}>
      <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
        <div style={{
          display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
          background: 'linear-gradient(135deg, #7c3aed, #3b82f6)',
          padding: '0.4rem 1.2rem', borderRadius: 999, marginBottom: '1rem',
          fontSize: '0.82rem', fontWeight: 700, letterSpacing: '0.08em', color: 'white',
        }}>
          <Zap size={14} /> ELEVENLABS END-TO-END
        </div>
        <h2 style={{ fontSize: '1.8rem', margin: '0 0 0.5rem' }}>Direct AI Dubbing</h2>
        <p style={{ color: 'var(--text-muted)', margin: 0 }}>
          Upload your video — ElevenLabs handles everything. No transcription step needed.
        </p>
      </div>

      {/* Drop Zone */}
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleFileDrop}
        onClick={() => fileInputRef.current?.click()}
        style={{
          border: `2px dashed ${dragging ? '#7c3aed' : file ? '#22c55e' : 'rgba(139,92,246,0.4)'}`,
          borderRadius: 16, padding: '2.5rem 1.5rem', textAlign: 'center',
          cursor: 'pointer', marginBottom: '1.5rem',
          background: dragging ? 'rgba(124,58,237,0.08)' : file ? 'rgba(34,197,94,0.06)' : 'rgba(124,58,237,0.04)',
          transition: 'all 0.2s',
        }}
      >
        <input
          ref={fileInputRef} type="file"
          accept={ACCEPTED_TYPES.join(',')}
          style={{ display: 'none' }}
          onChange={e => e.target.files?.[0] && setFile(e.target.files[0])}
        />
        {file ? (
          <>
            <Film size={36} color="#22c55e" style={{ marginBottom: '0.75rem' }} />
            <p style={{ margin: 0, fontWeight: 600, color: '#22c55e' }}>{file.name}</p>
            <p style={{ margin: '0.25rem 0 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
              {(file.size / 1024 / 1024).toFixed(1)} MB · Click to change
            </p>
          </>
        ) : (
          <>
            <Upload size={36} color="rgba(139,92,246,0.7)" style={{ marginBottom: '0.75rem' }} />
            <p style={{ margin: 0, fontWeight: 600 }}>Drop your video or audio file here</p>
            <p style={{ margin: '0.4rem 0 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
              MP4, MOV, AVI, MKV, MP3, WAV · up to 1 GB
            </p>
          </>
        )}
      </div>

      {/* Language Selector */}
      <div className="form-group" style={{ marginBottom: '1.25rem' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <Globe2 size={15} /> Target Language
        </label>
        <select
          value={targetLang}
          onChange={e => setTargetLang(e.target.value)}
          style={{ background: 'rgba(15,23,42,0.8)' }}
        >
          {LANGUAGES.map(l => (
            <option key={l.code} value={l.code}>{l.flag} {l.name} ({l.code})</option>
          ))}
        </select>
      </div>

      {/* Advanced Options */}
      <div style={{
        padding: '1rem 1.25rem', borderRadius: 12,
        border: '1px solid var(--border-light)', background: 'rgba(0,0,0,0.15)',
        marginBottom: '1.5rem',
      }}>
        <p style={{ margin: '0 0 0.75rem', fontSize: '0.85rem', color: 'var(--text-muted)', fontWeight: 600 }}>
          Advanced Options
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.25rem', alignItems: 'center' }}>
          <div className="form-group" style={{ margin: 0, flex: '1 1 180px' }}>
            <label style={{ fontSize: '0.82rem' }}>Speakers (0 = auto-detect)</label>
            <select
              value={numSpeakers}
              onChange={e => setNumSpeakers(Number(e.target.value))}
              style={{ background: 'rgba(15,23,42,0.8)', padding: '0.4rem 0.6rem' }}
            >
              {[0,1,2,3,4,5,6,7,8,9].map(n => (
                <option key={n} value={n}>{n === 0 ? '0 — Auto' : n}</option>
              ))}
            </select>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem', color: 'var(--text-muted)', cursor: 'pointer', flex: '1 1 220px' }}>
            <input
              type="checkbox"
              checked={disableCloning}
              onChange={e => setDisableCloning(e.target.checked)}
            />
            Disable voice cloning (use generic voice)
          </label>
        </div>
      </div>

      {/* Start Button */}
      <button
        id="direct-dub-start-btn"
        className="btn"
        onClick={handleStart}
        disabled={!file}
        style={{
          width: '100%', background: 'linear-gradient(135deg, #7c3aed, #3b82f6)',
          fontSize: '1rem', padding: '0.85rem',
          opacity: file ? 1 : 0.5,
        }}
      >
        <Zap size={18} /> Start ElevenLabs Dubbing → {langFlag} {langName}
      </button>

      <p style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.82rem', marginTop: '0.75rem' }}>
        Requires ElevenLabs Creator plan or higher for audio files. Video files supported on all plans (watermark may apply).
      </p>
    </div>
  );

  /* ─── PROCESSING PHASE ─── */
  if (phase === 'processing') return (
    <div style={{ maxWidth: 560, margin: '0 auto', textAlign: 'center' }}>
      <div style={{
        width: 80, height: 80, borderRadius: '50%', margin: '0 auto 1.5rem',
        background: 'linear-gradient(135deg, #7c3aed, #3b82f6)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        animation: 'spin 2s linear infinite',
      }}>
        <Loader2 size={36} color="white" />
      </div>
      <h2 style={{ fontSize: '1.6rem', marginBottom: '0.5rem' }}>Dubbing in Progress</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '2rem' }}>{statusText}</p>

      <div style={{
        background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.3)',
        borderRadius: 12, padding: '1.25rem', marginBottom: '1.5rem',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem', marginBottom: '0.75rem' }}>
          <span style={{ color: 'var(--text-muted)' }}>File</span>
          <span style={{ fontWeight: 600, maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file?.name}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem', marginBottom: '0.75rem' }}>
          <span style={{ color: 'var(--text-muted)' }}>Target Language</span>
          <span style={{ fontWeight: 600 }}>{langFlag} {langName}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem', marginBottom: '0.75rem' }}>
          <span style={{ color: 'var(--text-muted)' }}>Dubbing ID</span>
          <span style={{ fontFamily: 'monospace', fontSize: '0.78rem', color: '#a78bfa' }}>{dubbingId || '…'}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem' }}>
          <span style={{ color: 'var(--text-muted)' }}>Elapsed</span>
          <span style={{ fontWeight: 600, color: '#22c55e' }}>{formatElapsed(elapsed)}</span>
        </div>
      </div>

      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
        ElevenLabs is separating speakers, translating, and cloning voices.<br />
        Typical wait: <strong>1–5 minutes</strong> depending on video length.
      </p>
    </div>
  );

  /* ─── ERROR PHASE ─── */
  if (phase === 'error') return (
    <div style={{ maxWidth: 560, margin: '0 auto', textAlign: 'center' }}>
      <div style={{
        width: 72, height: 72, borderRadius: '50%', margin: '0 auto 1.5rem',
        background: 'rgba(239,68,68,0.15)', border: '2px solid rgba(239,68,68,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <AlertTriangle size={32} color="#ef4444" />
      </div>
      <h2 style={{ color: '#ef4444', marginBottom: '0.75rem' }}>Dubbing Failed</h2>
      <div style={{
        background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)',
        borderRadius: 10, padding: '1rem', marginBottom: '1.5rem',
        fontSize: '0.9rem', color: '#fca5a5', textAlign: 'left',
      }}>
        {errorMsg}
      </div>
      <button className="btn" onClick={handleReset} style={{ background: 'var(--accent)' }}>
        <RefreshCw size={18} /> Try Again
      </button>
    </div>
  );

  /* ─── COMPLETE PHASE ─── */
  return (
    <div style={{ maxWidth: 700, margin: '0 auto' }}>
      <div style={{ textAlign: 'center', marginBottom: '1.5rem' }}>
        <div style={{
          width: 72, height: 72, borderRadius: '50%', margin: '0 auto 1rem',
          background: 'rgba(34,197,94,0.15)', border: '2px solid rgba(34,197,94,0.5)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <CheckCircle size={36} color="#22c55e" />
        </div>
        <h2 style={{ fontSize: '1.7rem', marginBottom: '0.4rem' }}>Dubbing Complete! 🎉</h2>
        <p style={{ color: 'var(--text-muted)' }}>
          {langFlag} {langName} dub ready · ElevenLabs processed in {formatElapsed(elapsed)}
        </p>
      </div>

      {finalVideoUrl && (
        <div style={{ marginBottom: '1.5rem' }}>
          <h3 style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginBottom: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Dubbed Video</h3>
          <video
            src={finalVideoUrl} controls
            style={{ width: '100%', borderRadius: 12, background: 'black', maxHeight: 420 }}
          />
        </div>
      )}

      {finalAudioUrl && (
        <div style={{ marginBottom: '1.5rem' }}>
          <h3 style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginBottom: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Dubbed Audio</h3>
          <audio src={finalAudioUrl} controls style={{ width: '100%' }} />
        </div>
      )}

      <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center', flexWrap: 'wrap' }}>
        {finalVideoUrl && (
          <a href={finalVideoUrl} download={`dubbed_${langName.toLowerCase()}.mp4`}
            className="btn" style={{ background: 'linear-gradient(135deg,#7c3aed,#3b82f6)', textDecoration: 'none' }}>
            <Download size={18} /> Download Video
          </a>
        )}
        {finalAudioUrl && (
          <a href={finalAudioUrl} download={`dubbed_${langName.toLowerCase()}.wav`}
            className="btn btn-secondary" style={{ textDecoration: 'none' }}>
            <Download size={18} /> Download Audio
          </a>
        )}
        <button className="btn btn-secondary" onClick={handleReset}>
          <RefreshCw size={18} /> Dub Another
        </button>
      </div>
    </div>
  );
}
