import React, { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { Upload, FileAudio, Loader2 } from 'lucide-react';
import {
  estimateSttSecondsFromDuration,
  formatEtaRange,
  formatElapsedClock,
} from '../timeEstimates';

export default function Stage1Upload({ apiBase, onComplete }) {
  const [file, setFile] = useState(null);
  const [sourceLang, setSourceLang] = useState('hi-IN');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [timingCfg, setTimingCfg] = useState(null);
  const [videoDurationSec, setVideoDurationSec] = useState(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const fileInputRef = useRef(null);
  const uploadStartRef = useRef(null);
  const elapsedTimerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await axios.get(`${apiBase}/config`);
        if (!cancelled && res.data?.timing) {
          setTimingCfg(res.data.timing);
        }
      } catch {
        /* Estimators fall back to defaults when config is unavailable. */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  useEffect(() => {
    if (!file) {
      setVideoDurationSec(null);
      return undefined;
    }
    let cancelled = false;
    const url = URL.createObjectURL(file);
    const v = document.createElement('video');
    v.preload = 'metadata';
    v.onloadedmetadata = () => {
      if (cancelled) return;
      const d = v.duration;
      setVideoDurationSec(Number.isFinite(d) && d > 0 ? d : null);
    };
    v.onerror = () => {
      if (!cancelled) setVideoDurationSec(null);
    };
    v.src = url;
    return () => {
      cancelled = true;
      v.removeAttribute('src');
      URL.revokeObjectURL(url);
    };
  }, [file]);

  useEffect(() => {
    if (!loading) {
      if (elapsedTimerRef.current) {
        clearInterval(elapsedTimerRef.current);
        elapsedTimerRef.current = null;
      }
      setElapsedSec(0);
      return undefined;
    }
    uploadStartRef.current = Date.now();
    elapsedTimerRef.current = setInterval(() => {
      if (uploadStartRef.current) {
        setElapsedSec((Date.now() - uploadStartRef.current) / 1000);
      }
    }, 500);
    return () => {
      if (elapsedTimerRef.current) {
        clearInterval(elapsedTimerRef.current);
        elapsedTimerRef.current = null;
      }
    };
  }, [loading]);

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
        setFile(e.target.files[0]);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setLoading(true);
    setError('');

    const formData = new FormData();
    formData.append('file', file);
    if (sourceLang !== 'auto') {
      formData.append('language_code', sourceLang);
    }

    try {
      const res = await axios.post(`${apiBase}/upload`, formData, {
        // STT can run a long time; 0 = no axios-side cap (browser/OS may still limit).
        timeout: 0,
      });
      // Ensure we have a uniform block structure
      const rawData = res.data.data;
      const detectedLang = rawData?.language_code || rawData?.language || rawData?.detected_language_code || null;
      let blocks = [];
      // Use the 'diarized_transcript.entries' array if provided by Sarvam STT
      const entries = rawData?.diarized_transcript?.entries;
      if (entries && entries.length > 0) {
          blocks = entries.map((sent, i) => ({
              id: `block-${i}`,
              speakers: [`S${sent.speaker_id || '?'}`],
              transcript: sent.transcript,
              timestamps: [sent.start_time_seconds, sent.end_time_seconds]
          }));
      } else if (rawData && rawData.transcript) {
          // Fallback if there are no sentences but we have a master transcript
          blocks = [{
              id: 'block-1',
              speakers: ['S1'],
              transcript: rawData.transcript,
              timestamps: [0, 5000]
          }];
      } else {
         // Create mock data for demo since Sarvam STT can take long
          blocks = [
              { id: 1, speakers: ['S1'], transcript: "Hello, welcome to the pipeline.", timestamps: [0, 2000] },
              { id: 2, speakers: ['S2'], transcript: "It is great to be here.", timestamps: [2500, 4500] }
          ];
      }
      const resolvedSourceLang = sourceLang === 'auto' ? (detectedLang || 'hi-IN') : sourceLang;
      onComplete(blocks, file, res.data.session_id, resolvedSourceLang);
    } catch (err) {
      console.error(err);
      const code = err?.code;
      const noResponse = !err?.response;
      let detail = err?.response?.data?.detail || err.message || 'Unknown error';
      if (noResponse && (code === 'ERR_NETWORK' || err.message === 'Network Error')) {
        detail =
          'Connection was lost (often net::ERR_CONNECTION_RESET). The server may have stopped mid-request — ' +
          'check the uvicorn terminal for a crash or traceback. Long STT jobs can also hit OS or proxy limits; ' +
          'try a shorter clip or restart the backend.';
      }
      setError(`Upload/STT failed: ${detail}`);
      // Do NOT pass mock data - show the real error to the user
    } finally {
      setLoading(false);
    }
  };

  const sttEtaSeconds = estimateSttSecondsFromDuration(videoDurationSec, timingCfg);

  return (
    <div className="text-center">
      <h2 style={{fontSize: '1.5rem', marginBottom: '1rem'}}>Stage 1: Upload Video</h2>
      <p style={{color: 'var(--text-muted)', marginBottom: '2rem'}}>
        Upload an MP4 file. The pipeline will prepare the audio for translation and dubbing.
      </p>

      <div 
        className="upload-zone"
        onClick={() => fileInputRef.current?.click()}
      >
        <input 
          type="file" 
          accept=".mp4,.mov,.avi" 
          hidden 
          ref={fileInputRef}
          onChange={handleFileChange}
        />
        {file ? (
          <div>
            <FileAudio size={48} className="upload-icon mx-auto" />
            <p className="mt-2 text-lg">{file.name}</p>
          </div>
        ) : (
          <div>
            <Upload size={48} className="upload-icon mx-auto" style={{ margin: '0 auto' }} />
            <p style={{ marginTop: '1rem' }}>Click or drag file to upload</p>
          </div>
        )}
      </div>

      <div style={{ marginTop: '1.5rem', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.5rem' }}>
         <label style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>Original Audio Language</label>
         <select 
           value={sourceLang} 
           onChange={(e) => setSourceLang(e.target.value)}
           style={{ background: 'rgba(15, 23, 42, 0.8)', border: '1px solid var(--border-light)', padding: '0.5rem', borderRadius: '8px', color: 'white', minWidth: '200px' }}
         >
           <option value="auto">Auto-Detect</option>
           <option value="hi-IN">Hindi (hi-IN)</option>
           <option value="en-IN">English (en-IN)</option>
           <option value="bn-IN">Bengali (bn-IN)</option>
           <option value="ta-IN">Tamil (ta-IN)</option>
           <option value="te-IN">Telugu (te-IN)</option>
           <option value="mr-IN">Marathi (mr-IN)</option>
           <option value="gu-IN">Gujarati (gu-IN)</option>
           <option value="kn-IN">Kannada (kn-IN)</option>
           <option value="ml-IN">Malayalam (ml-IN)</option>
           <option value="pa-IN">Punjabi (pa-IN)</option>
         </select>
      </div>

      {error && <p style={{color: '#ef4444', marginTop: '1rem'}}>{error}</p>}

      {file && !loading && Number.isFinite(videoDurationSec) && videoDurationSec > 0 && (
        <p style={{ color: 'var(--text-muted)', marginTop: '1rem', fontSize: '0.9rem' }}>
          Detected video length {Math.ceil(videoDurationSec / 60)} min — typical STT wait{' '}
          <strong>{formatEtaRange(sttEtaSeconds)}</strong> (rough guide; queue load varies).
        </p>
      )}

      <div style={{marginTop: '2rem'}}>
        <button 
          className="btn" 
          onClick={handleUpload} 
          disabled={!file || loading}
        >
          {loading ? (
            <>
              <Loader2 className="loader" size={20} />
              Processing (Extracting Audio & Preparing)...
            </>
          ) : 'Upload & Prepare for Translation'}
        </button>
        {loading && (
          <p
            style={{
              color: 'var(--text-muted)',
              marginTop: '1rem',
              fontSize: '0.92rem',
              maxWidth: '420px',
              marginLeft: 'auto',
              marginRight: 'auto',
            }}
          >
            Estimated STT (diarized batch){' '}
            <strong>{formatEtaRange(sttEtaSeconds)}</strong>
            {' · '}
            Elapsed <strong>{formatElapsedClock(elapsedSec)}</strong>
          </p>
        )}
      </div>
    </div>
  );
}
