import React, { useState, useMemo, useRef, useEffect } from 'react';
import axios from 'axios';
import { CheckCircle, PlayCircle, X, Edit3, User } from 'lucide-react';

const FALLBACK_LANGUAGES = [
  { code: 'hi', name: 'Hindi' },
  { code: 'ta', name: 'Tamil' },
  { code: 'te', name: 'Telugu' },
  { code: 'bn', name: 'Bengali' },
  { code: 'mr', name: 'Marathi' },
  { code: 'gu', name: 'Gujarati' },
  { code: 'kn', name: 'Kannada' },
  { code: 'ml', name: 'Malayalam' },
  { code: 'pa', name: 'Punjabi' },
];
const INDIAN_TRANSLATION_CODES = new Set(['hi', 'ta', 'te', 'bn', 'mr', 'gu', 'kn', 'ml', 'pa']);

function fmtTime(s) {
  const m = Math.floor(s / 60);
  const sec = s.toFixed(1);
  return m > 0 ? `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}` : `${sec}s`;
}

export default function Stage2Transcript({ apiBase, blocks: initialBlocks, videoFile, sourceLang, onComplete }) {
  const [blocks, setBlocks]           = useState(() => initialBlocks.map((b, i) => ({ ...b, _key: i })));
  const [speakerNames, setSpeakerNames] = useState({});   // { S0: "Narrator", S1: "Guest" }
  const [languages, setLanguages]     = useState(FALLBACK_LANGUAGES);
  const [languagesLoading, setLanguagesLoading] = useState(true);
  const [targetLang, setTargetLang]   = useState('hi');
  const [langPickerOpen, setLangPickerOpen] = useState(false);
  const [editingName, setEditingName] = useState(null);   // speaker id being renamed
  const [nameInput, setNameInput]     = useState('');
  const [playingVideo, setPlayingVideo] = useState(false);
  const [seekTime, setSeekTime]       = useState(0);
  const [previewEndTime, setPreviewEndTime] = useState(null);
  const videoRef    = useRef(null);
  const playTimer   = useRef(null);
  const videoUrl    = useMemo(() => videoFile ? URL.createObjectURL(videoFile) : null, [videoFile]);

  useEffect(() => () => { if (videoUrl) URL.revokeObjectURL(videoUrl); }, [videoUrl]);

  // Fetch Gemini translation languages, filtered to Indian languages only.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await axios.get(`${apiBase}/translate/languages`);
        const list = (res.data?.languages || [])
          .filter((l) => INDIAN_TRANSLATION_CODES.has(l.code))
          .map((l) => ({ code: l.code, name: l.name, flag: l.flag }));
        if (!cancelled && list.length) {
          setLanguages(list);
          if (!list.some((l) => l.code === targetLang)) {
            setTargetLang(list[0].code);
          }
        }
      } catch (err) {
        console.warn('Could not load parrot translate language list — using fallback.', err);
      } finally {
        if (!cancelled) setLanguagesLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [apiBase]);

  // Unique speakers in order of appearance
  const uniqueSpeakers = useMemo(() => {
    const seen = new Set();
    const list = [];
    for (const b of blocks) {
      const s = b.speakers?.[0];
      if (s && !seen.has(s)) { seen.add(s); list.push(s); }
    }
    return list;
  }, [blocks]);

  const displayName = (id) => speakerNames[id] || id;

  const startRename = (id) => {
    setEditingName(id);
    setNameInput(speakerNames[id] || id);
  };

  const commitRename = () => {
    if (editingName && nameInput.trim()) {
      const newName = nameInput.trim();
      setSpeakerNames((prev) => ({ ...prev, [editingName]: newName }));
      // Also update blocks that reference this speaker
      setBlocks((prev) =>
        prev.map((b) =>
          b.speakers?.[0] === editingName ? { ...b, speakers: [editingName] } : b
        )
      );
    }
    setEditingName(null);
    setNameInput('');
  };

  const updateTranscript = (key, value) => {
    setBlocks((prev) => prev.map((b) => b._key === key ? { ...b, transcript: value } : b));
  };

  const playSpeaker = (speakerId) => {
    const speakerBlocks = blocks.filter((b) => b.speakers?.[0] === speakerId);
    const firstValid = speakerBlocks.find((b) => {
      const t0 = Number(b.timestamps?.[0]);
      const t1 = Number(b.timestamps?.[1]);
      return Number.isFinite(t0) && Number.isFinite(t1) && t1 > t0;
    });
    const first = firstValid || speakerBlocks[0];
    if (!first || !videoRef.current) return;
    clearTimeout(playTimer.current);
    const t = Number(first.timestamps?.[0]);
    const start = Number.isFinite(t) && t >= 0 ? t : 0;
    const end = Number(first.timestamps?.[1]);
    const safeEnd = Number.isFinite(end) && end > start ? Math.min(end, start + 8) : (start + 5);
    setSeekTime(start);
    setPreviewEndTime(safeEnd);
    setPlayingVideo(true);
    const video = videoRef.current;

    const startPlayback = () => {
      try {
        video.currentTime = start;
      } catch (_) {
        // Ignore seek timing race; loadedmetadata handler retries with seekTime.
      }
      video.play().catch(() => {});
    };

    if (video.readyState >= 1) {
      startPlayback();
    }

    const previewMs = Math.max(3000, Math.min(8000, (safeEnd - start) * 1000));
    playTimer.current = setTimeout(() => {
      videoRef.current?.pause();
      setPlayingVideo(false);
      setPreviewEndTime(null);
    }, previewMs);
  };

  const stopPreview = () => {
    clearTimeout(playTimer.current);
    videoRef.current?.pause();
    setPlayingVideo(false);
    setPreviewEndTime(null);
  };

  const handleVideoLoadedMetadata = () => {
    if (!playingVideo || !videoRef.current) return;
    try {
      videoRef.current.currentTime = seekTime;
    } catch (_) {
      return;
    }
    videoRef.current.play().catch(() => {});
  };

  const handleVideoTimeUpdate = () => {
    if (!playingVideo || !videoRef.current || previewEndTime == null) return;
    if (videoRef.current.currentTime >= previewEndTime) {
      stopPreview();
    }
  };

  const handleConfirm = () => {
    // Pass blocks with display names applied to speakers field
    const namedBlocks = blocks.map((b) => ({
      ...b,
      speakers: b.speakers?.map((s) => speakerNames[s] || s) || b.speakers,
      _originalSpeakerId: b.speakers?.[0],
    }));
    onComplete(namedBlocks, targetLang, sourceLang);
  };

  const blocksBySpeaker = useMemo(() => {
    const map = {};
    for (const b of blocks) {
      const s = b.speakers?.[0] || 'S?';
      map[s] = (map[s] || 0) + 1;
    }
    return map;
  }, [blocks]);

  return (
    <div className="stage-container">
      <div className="stage-header">
        <span className="stage-badge">2</span>
        <div>
          <h2 className="stage-title">Review Transcript</h2>
          <p className="stage-subtitle">Edit text, rename speakers, then pick your target dub language</p>
        </div>
      </div>

      {/* Speaker Cards */}
      <div className="speaker-grid">
        {uniqueSpeakers.map((sid) => (
          <div key={sid} className="speaker-card">
            <div className="speaker-card-top">
              <div className="speaker-avatar">
                <User size={16} />
              </div>
              <div className="speaker-info">
                {editingName === sid ? (
                  <div className="rename-row">
                    <input
                      className="rename-input"
                      value={nameInput}
                      onChange={(e) => setNameInput(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && commitRename()}
                      autoFocus
                    />
                    <button className="btn-icon" onClick={commitRename}><CheckCircle size={14} /></button>
                    <button className="btn-icon ghost" onClick={() => setEditingName(null)}><X size={14} /></button>
                  </div>
                ) : (
                  <div className="name-row">
                    <span className="speaker-name">{displayName(sid)}</span>
                  </div>
                )}
                {editingName !== sid && (
                  <div className="speaker-actions">
                    {videoUrl && (
                      <button
                        className="btn-ref"
                        onClick={() => playSpeaker(sid)}
                        title="Preview reference voice"
                      >
                        <PlayCircle size={13} /> Reference
                      </button>
                    )}
                    <button className="btn-icon ghost" onClick={() => startRename(sid)} title="Rename speaker">
                      <Edit3 size={13} />
                    </button>
                  </div>
                )}
                <span className="speaker-meta">{blocksBySpeaker[sid] || 0} segments</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Transcript table */}
      <div className="transcript-table">
        <div className="tt-header">
          <span>Time</span>
          <span>Speaker</span>
          <span>Transcript (editable)</span>
        </div>
        <div className="tt-body">
          {blocks.map((b) => {
            const sid  = b.speakers?.[0] || 'S?';
            const t0   = Number(b.timestamps?.[0] || 0);
            const t1   = Number(b.timestamps?.[1] || 0);
            return (
              <div key={b._key} className="tt-row">
                <span className="tt-time">{fmtTime(t0)}–{fmtTime(t1)}</span>
                <span className="tt-speaker">{displayName(sid)}</span>
                <textarea
                  className="tt-text"
                  value={b.transcript}
                  rows={Math.max(1, Math.ceil(b.transcript.length / 60))}
                  onChange={(e) => updateTranscript(b._key, e.target.value)}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* Target language picker */}
      <div className="field-group">
        <label className="field-label">
          Dub into language {languagesLoading && <span style={{ opacity: 0.6 }}>(loading…)</span>}
        </label>
        <button
          type="button"
          className="lang-picker-trigger"
          onClick={() => setLangPickerOpen((v) => !v)}
          disabled={languagesLoading}
          aria-expanded={langPickerOpen}
          aria-label="Choose dub language"
        >
          {languages.find((l) => l.code === targetLang)?.name || targetLang}
          <span className="lang-picker-caret">{langPickerOpen ? '▴' : '▾'}</span>
        </button>
        {langPickerOpen && (
          <div className={`lang-scroll-list ${languagesLoading ? 'disabled' : ''}`} role="listbox" aria-label="Dub language">
            {languages.map((l) => (
              <button
                key={l.code}
                type="button"
                className={`lang-option ${targetLang === l.code ? 'selected' : ''}`}
                onClick={() => {
                  setTargetLang(l.code);
                  setLangPickerOpen(false);
                }}
                disabled={languagesLoading}
                aria-selected={targetLang === l.code}
              >
                <span>{l.flag ? `${l.flag} ` : ''}{l.name}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <button className="btn-primary" onClick={handleConfirm}>
        <CheckCircle size={18} />
        Confirm &amp; Translate →
      </button>

      {/* Centered video preview overlay (video element stays mounted so the ref is stable) */}
      {videoUrl && (
        <div
          className={`video-preview-overlay ${playingVideo ? 'visible' : 'hidden'}`}
          onClick={stopPreview}
        >
          <div className="video-preview-modal" onClick={(e) => e.stopPropagation()}>
            <div className="vpm-header">
              <span>Speaker preview</span>
              <button className="btn-icon ghost" onClick={stopPreview}><X size={15} /></button>
            </div>
            <video
              ref={videoRef}
              src={videoUrl}
              style={{ width: '100%', borderRadius: 8 }}
              controls
              preload="metadata"
              playsInline
              onLoadedMetadata={handleVideoLoadedMetadata}
              onTimeUpdate={handleVideoTimeUpdate}
            />
          </div>
        </div>
      )}
    </div>
  );
}