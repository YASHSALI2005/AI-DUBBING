import React, { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { Upload, FileAudio, Loader2, AlertCircle } from 'lucide-react';

const SUPPORTED_LANGS = [
  { code: '',      name: 'Auto-detect' },
  { code: 'en',   name: 'English' },
  { code: 'hi',   name: 'Hindi' },
  { code: 'es',   name: 'Spanish' },
  { code: 'fr',   name: 'French' },
  { code: 'de',   name: 'German' },
  { code: 'ja',   name: 'Japanese' },
  { code: 'zh',   name: 'Chinese' },
  { code: 'ar',   name: 'Arabic' },
  { code: 'pt',   name: 'Portuguese' },
  { code: 'it',   name: 'Italian' },
  { code: 'ko',   name: 'Korean' },
  { code: 'ta',   name: 'Tamil' },
  { code: 'te',   name: 'Telugu' },
  { code: 'bn',   name: 'Bengali' },
  { code: 'mr',   name: 'Marathi' },
  { code: 'gu',   name: 'Gujarati' },
  { code: 'kn',   name: 'Kannada' },
  { code: 'ml',   name: 'Malayalam' },
  { code: 'pa',   name: 'Punjabi' },
];

const ACCEPTED_EXTS = '.mp4,.mov,.avi,.mp3,.wav,.m4a,.webm';

export default function Stage1Upload({ apiBase, onComplete }) {
  const [file, setFile]             = useState(null);
  const [sourceLang, setSourceLang] = useState('');
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState('');
  const [elapsed, setElapsed]       = useState(0);
  const [videoDuration, setVideoDuration] = useState(null);

  const fileInputRef  = useRef(null);
  const timerRef      = useRef(null);
  const startTimeRef  = useRef(null);

  // Measure video duration for ETA hint
  useEffect(() => {
    if (!file) { setVideoDuration(null); return; }
    const url = URL.createObjectURL(file);
    const el  = document.createElement('video');
    el.preload = 'metadata';
    el.onloadedmetadata = () => {
      setVideoDuration(Number.isFinite(el.duration) && el.duration > 0 ? el.duration : null);
    };
    el.onerror = () => setVideoDuration(null);
    el.src = url;
    return () => { el.removeAttribute('src'); URL.revokeObjectURL(url); };
  }, [file]);

  // Elapsed timer while loading
  useEffect(() => {
    if (loading) {
      startTimeRef.current = Date.now();
      timerRef.current = setInterval(() => {
        setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000));
      }, 500);
    } else {
      clearInterval(timerRef.current);
      setElapsed(0);
    }
    return () => clearInterval(timerRef.current);
  }, [loading]);

  const handleDrop = (e) => {
    e.preventDefault();
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) setFile(dropped);
  };

  const handleUpload = async () => {
    if (!file) return;
    setLoading(true);
    setError('');

    const formData = new FormData();
    formData.append('file', file);
    if (sourceLang) formData.append('language_code', sourceLang);

    try {
      const res = await axios.post(`${apiBase}/upload`, formData, { timeout: 0 });
      const { blocks, session_id, detected_language, audio_duration_seconds } = res.data;
      onComplete(blocks, file, session_id, detected_language || sourceLang || 'en', audio_duration_seconds);
    } catch (err) {
      console.error(err);
      const detail = err?.response?.data?.detail || err.message || 'Unknown error';
      setError(`Upload / STT failed: ${detail}`);
    } finally {
      setLoading(false);
    }
  };

  const fmtDur = (s) => s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;

  return (
    <div className="stage-container">
      <div className="stage-header">
        <span className="stage-badge">1</span>
        <div>
          <h2 className="stage-title">Upload</h2>
          <p className="stage-subtitle">Video or audio file —  will transcribe &amp; diarize speakers</p>
        </div>
      </div>

      {/* Drop zone */}
      <div
        className={`upload-zone ${file ? 'has-file' : ''}`}
        onClick={() => !loading && fileInputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
      >
        <input
          type="file"
          accept={ACCEPTED_EXTS}
          hidden
          ref={fileInputRef}
          onChange={(e) => e.target.files?.[0] && setFile(e.target.files[0])}
        />
        {file ? (
          <div className="upload-file-info">
            <FileAudio size={40} className="upload-icon" />
            <p className="upload-filename">{file.name}</p>
            {videoDuration && (
              <p className="upload-meta">{Math.round(videoDuration)}s · {(file.size / 1024 / 1024).toFixed(1)} MB</p>
            )}
          </div>
        ) : (
          <div className="upload-empty">
            <Upload size={40} className="upload-icon" />
            <p className="upload-hint">Click or drag to upload</p>
            <p className="upload-meta">MP4 · MOV · AVI · MP3 · WAV · M4A</p>
          </div>
        )}
      </div>

      {/* Language selector */}
      <div className="field-group">
        <label className="field-label">Source audio language</label>
        <select
          className="field-select"
          value={sourceLang}
          onChange={(e) => setSourceLang(e.target.value)}
          disabled={loading}
        >
          {SUPPORTED_LANGS.map((l) => (
            <option key={l.code} value={l.code}>{l.name}</option>
          ))}
        </select>
      </div>

      {/* Error */}
      {error && (
        <div className="error-banner">
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* ETA */}
      {file && !loading && videoDuration && (
        <p className="eta-hint">
          ~{Math.ceil(videoDuration / 60)} min video — STT usually takes 20–60s
        </p>
      )}

      {/* Submit */}
      <button className="btn-primary" onClick={handleUpload} disabled={!file || loading}>
        {loading ? (
          <>
            <Loader2 size={18} className="spin" />
            Transcribing&hellip; &nbsp;{fmtDur(elapsed)}
          </>
        ) : (
          <>
            <Upload size={18} />
            Upload &amp; Transcribe
          </>
        )}
      </button>

      {loading && (
        <p className="progress-note">
           Scribe is diarizing speakers — this usually takes 15–60s
        </p>
      )}
    </div>
  );
}