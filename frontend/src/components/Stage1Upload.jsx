import React, { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { Upload, FileAudio, Loader2, AlertCircle } from 'lucide-react';

const SUPPORTED_LANGS = [
  { code: '',      name: 'Auto-detect' },
  { code: 'afr', name: 'Afrikaans' },
  { code: 'amh', name: 'Amharic' },
  { code: 'ara', name: 'Arabic' },
  { code: 'hye', name: 'Armenian' },
  { code: 'asm', name: 'Assamese' },
  { code: 'ast', name: 'Asturian' },
  { code: 'aze', name: 'Azerbaijani' },
  { code: 'bel', name: 'Belarusian' },
  { code: 'ben', name: 'Bengali' },
  { code: 'bos', name: 'Bosnian' },
  { code: 'bul', name: 'Bulgarian' },
  { code: 'mya', name: 'Burmese' },
  { code: 'yue', name: 'Cantonese' },
  { code: 'cat', name: 'Catalan' },
  { code: 'ceb', name: 'Cebuano' },
  { code: 'nya', name: 'Chichewa' },
  { code: 'hrv', name: 'Croatian' },
  { code: 'ces', name: 'Czech' },
  { code: 'dan', name: 'Danish' },
  { code: 'nld', name: 'Dutch' },
  { code: 'eng', name: 'English' },
  { code: 'est', name: 'Estonian' },
  { code: 'fil', name: 'Filipino' },
  { code: 'fin', name: 'Finnish' },
  { code: 'fra', name: 'French' },
  { code: 'ful', name: 'Fulah' },
  { code: 'glg', name: 'Galician' },
  { code: 'lug', name: 'Ganda' },
  { code: 'kat', name: 'Georgian' },
  { code: 'deu', name: 'German' },
  { code: 'ell', name: 'Greek' },
  { code: 'guj', name: 'Gujarati' },
  { code: 'hau', name: 'Hausa' },
  { code: 'heb', name: 'Hebrew' },
  { code: 'hin', name: 'Hindi' },
  { code: 'hun', name: 'Hungarian' },
  { code: 'isl', name: 'Icelandic' },
  { code: 'ibo', name: 'Igbo' },
  { code: 'ind', name: 'Indonesian' },
  { code: 'gle', name: 'Irish' },
  { code: 'ita', name: 'Italian' },
  { code: 'jpn', name: 'Japanese' },
  { code: 'jav', name: 'Javanese' },
  { code: 'kea', name: 'Kabuverdianu' },
  { code: 'kan', name: 'Kannada' },
  { code: 'kaz', name: 'Kazakh' },
  { code: 'khm', name: 'Khmer' },
  { code: 'kor', name: 'Korean' },
  { code: 'kur', name: 'Kurdish' },
  { code: 'kir', name: 'Kyrgyz' },
  { code: 'lao', name: 'Lao' },
  { code: 'lav', name: 'Latvian' },
  { code: 'lin', name: 'Lingala' },
  { code: 'lit', name: 'Lithuanian' },
  { code: 'luo', name: 'Luo' },
  { code: 'ltz', name: 'Luxembourgish' },
  { code: 'mkd', name: 'Macedonian' },
  { code: 'msa', name: 'Malay' },
  { code: 'mal', name: 'Malayalam' },
  { code: 'mlt', name: 'Maltese' },
  { code: 'zho', name: 'Mandarin Chinese' },
  { code: 'mri', name: 'Maori' },
  { code: 'mar', name: 'Marathi' },
  { code: 'mon', name: 'Mongolian' },
  { code: 'nep', name: 'Nepali' },
  { code: 'nso', name: 'Northern Sotho' },
  { code: 'nor', name: 'Norwegian' },
  { code: 'oci', name: 'Occitan' },
  { code: 'ori', name: 'Odia' },
  { code: 'pus', name: 'Pashto' },
  { code: 'fas', name: 'Persian' },
  { code: 'pol', name: 'Polish' },
  { code: 'por', name: 'Portuguese' },
  { code: 'pan', name: 'Punjabi' },
  { code: 'ron', name: 'Romanian' },
  { code: 'rus', name: 'Russian' },
  { code: 'srp', name: 'Serbian' },
  { code: 'sna', name: 'Shona' },
  { code: 'snd', name: 'Sindhi' },
  { code: 'slk', name: 'Slovak' },
  { code: 'slv', name: 'Slovenian' },
  { code: 'som', name: 'Somali' },
  { code: 'spa', name: 'Spanish' },
  { code: 'swa', name: 'Swahili' },
  { code: 'swe', name: 'Swedish' },
  { code: 'tam', name: 'Tamil' },
  { code: 'tgk', name: 'Tajik' },
  { code: 'tel', name: 'Telugu' },
  { code: 'tha', name: 'Thai' },
  { code: 'tur', name: 'Turkish' },
  { code: 'ukr', name: 'Ukrainian' },
  { code: 'umb', name: 'Umbundu' },
  { code: 'urd', name: 'Urdu' },
  { code: 'uzb', name: 'Uzbek' },
  { code: 'vie', name: 'Vietnamese' },
  { code: 'cym', name: 'Welsh' },
  { code: 'wol', name: 'Wolof' },
  { code: 'xho', name: 'Xhosa' },
  { code: 'zul', name: 'Zulu' },
];

const ACCEPTED_EXTS = '.mp4,.mov,.avi,.mp3,.wav,.m4a,.webm';

export default function Stage1Upload({ apiBase, onComplete }) {
  const [file, setFile]             = useState(null);
  const [sourceLang, setSourceLang] = useState('');
  const [langPickerOpen, setLangPickerOpen] = useState(false);
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
        <button
          type="button"
          className="lang-picker-trigger"
          onClick={() => setLangPickerOpen((v) => !v)}
          disabled={loading}
          aria-expanded={langPickerOpen}
          aria-label="Choose source language"
        >
          {SUPPORTED_LANGS.find((l) => l.code === sourceLang)?.name || 'Auto-detect'}
          <span className="lang-picker-caret">{langPickerOpen ? '▴' : '▾'}</span>
        </button>
        {langPickerOpen && (
          <div className={`lang-scroll-list ${loading ? 'disabled' : ''}`} role="listbox" aria-label="Source audio language">
            {SUPPORTED_LANGS.map((l) => (
              <button
                key={l.code || 'auto'}
                type="button"
                className={`lang-option ${sourceLang === l.code ? 'selected' : ''}`}
                onClick={() => {
                  setSourceLang(l.code);
                  setLangPickerOpen(false);
                }}
                disabled={loading}
                aria-selected={sourceLang === l.code}
              >
                <span>{l.name}</span>
              </button>
            ))}
          </div>
        )}
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