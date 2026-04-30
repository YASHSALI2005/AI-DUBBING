import React, { useState, useMemo, useRef, useEffect } from 'react';
import { CheckCircle, PlayCircle, X, Edit3, User } from 'lucide-react';

const TARGET_LANGUAGES = [
  { code: 'hi', name: 'Hindi' },
  { code: 'en', name: 'English' },
  { code: 'es', name: 'Spanish' },
  { code: 'fr', name: 'French' },
  { code: 'de', name: 'German' },
  { code: 'ja', name: 'Japanese' },
  { code: 'zh', name: 'Chinese' },
  { code: 'ar', name: 'Arabic' },
  { code: 'pt', name: 'Portuguese' },
  { code: 'it', name: 'Italian' },
  { code: 'ko', name: 'Korean' },
  { code: 'ta', name: 'Tamil' },
  { code: 'te', name: 'Telugu' },
  { code: 'bn', name: 'Bengali' },
  { code: 'mr', name: 'Marathi' },
  { code: 'gu', name: 'Gujarati' },
  { code: 'kn', name: 'Kannada' },
  { code: 'ml', name: 'Malayalam' },
  { code: 'pa', name: 'Punjabi' },
  { code: 'nl', name: 'Dutch' },
  { code: 'pl', name: 'Polish' },
  { code: 'ru', name: 'Russian' },
  { code: 'tr', name: 'Turkish' },
  { code: 'sv', name: 'Swedish' },
  { code: 'id', name: 'Indonesian' },
  { code: 'vi', name: 'Vietnamese' },
  { code: 'uk', name: 'Ukrainian' },
];

function fmtTime(s) {
  const m = Math.floor(s / 60);
  const sec = s.toFixed(1);
  return m > 0 ? `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}` : `${sec}s`;
}

export default function Stage2Transcript({ blocks: initialBlocks, videoFile, sourceLang, onComplete }) {
  const [blocks, setBlocks]           = useState(() => initialBlocks.map((b, i) => ({ ...b, _key: i })));
  const [speakerNames, setSpeakerNames] = useState({});   // { S0: "Narrator", S1: "Guest" }
  const [targetLang, setTargetLang]   = useState('hi');
  const [editingName, setEditingName] = useState(null);   // speaker id being renamed
  const [nameInput, setNameInput]     = useState('');
  const [playingVideo, setPlayingVideo] = useState(false);
  const [seekTime, setSeekTime]       = useState(0);
  const videoRef    = useRef(null);
  const playTimer   = useRef(null);
  const videoUrl    = useMemo(() => videoFile ? URL.createObjectURL(videoFile) : null, [videoFile]);

  useEffect(() => () => { if (videoUrl) URL.revokeObjectURL(videoUrl); }, [videoUrl]);

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
    const first = blocks.find((b) => b.speakers?.[0] === speakerId);
    if (!first || !videoRef.current) return;
    clearTimeout(playTimer.current);
    const t = Number(first.timestamps?.[0] || 0);
    setSeekTime(t);
    setPlayingVideo(true);
    videoRef.current.currentTime = t;
    videoRef.current.play().catch(() => {});
    playTimer.current = setTimeout(() => {
      videoRef.current?.pause();
      setPlayingVideo(false);
    }, 5000);
  };

  const stopPreview = () => {
    clearTimeout(playTimer.current);
    videoRef.current?.pause();
    setPlayingVideo(false);
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
                    <button className="btn-icon ghost" onClick={() => startRename(sid)} title="Rename">
                      <Edit3 size={13} />
                    </button>
                  </div>
                )}
                <span className="speaker-meta">{blocksBySpeaker[sid] || 0} segments</span>
              </div>
            </div>
            {videoUrl && (
              <button className="btn-ref" onClick={() => playSpeaker(sid)}>
                <PlayCircle size={14} /> Preview voice
              </button>
            )}
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

      {/* Target language picker */}
      <div className="field-group">
        <label className="field-label">Dub into language</label>
        <select
          className="field-select"
          value={targetLang}
          onChange={(e) => setTargetLang(e.target.value)}
        >
          {TARGET_LANGUAGES.map((l) => (
            <option key={l.code} value={l.code}>{l.name}</option>
          ))}
        </select>
      </div>

      <button className="btn-primary" onClick={handleConfirm}>
        <CheckCircle size={18} />
        Confirm &amp; Translate →
      </button>

      {/* Floating video preview */}
      {videoUrl && (
        <div className={`video-preview-modal ${playingVideo ? 'visible' : ''}`}>
          <div className="vpm-header">
            <span>Speaker preview</span>
            <button className="btn-icon ghost" onClick={stopPreview}><X size={15} /></button>
          </div>
          <video ref={videoRef} src={videoUrl} style={{ width: '100%', borderRadius: 8 }} controls />
        </div>
      )}
    </div>
  );
}