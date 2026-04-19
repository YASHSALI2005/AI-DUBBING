import React, { useState, useMemo, useRef, useEffect } from 'react';
import axios from 'axios';
import { Mic, Loader2, Wand2, PlayCircle, X, Film, Volume2 } from 'lucide-react';

// Speaker → voice mapping for Gemini native TTS (gemini branch; ElevenLabs disabled on backend).
const AVAILABLE_VOICES = [
  { id: 'auto',         name: 'Auto (Recommended)' },
  // Gemini Female Voices
  { id: 'Kore',         name: 'Kore (Female) - Firm' },
  { id: 'Aoede',        name: 'Aoede (Female) - Breezy' },
  { id: 'Zephyr',       name: 'Zephyr (Female) - Bright' },
  { id: 'Leda',         name: 'Leda (Female) - Professional' },
  { id: 'Autonoe',      name: 'Autonoe (Female) - Rich' },
  { id: 'Despina',      name: 'Despina (Female) - Confident' },
  { id: 'Vindemiatrix', name: 'Vindemiatrix (Female) - Steady' },
  // Gemini Male/Other Voices
  { id: 'Puck',         name: 'Puck (Male) - Upbeat' },
  { id: 'Charon',       name: 'Charon (Male) - Informative' },
  { id: 'Fenrir',       name: 'Fenrir (Male) - Excitable' },
  { id: 'Orus',         name: 'Orus (Male) - Energetic' },
  { id: 'Iapetus',      name: 'Iapetus (Male) - Mature' },
  { id: 'Achird',       name: 'Achird (Male) - Warm' },
  { id: 'Algenib',      name: 'Algenib (Male) - Narrative' },
  { id: 'Schedar',      name: 'Schedar (Male) - Crisp' },
  { id: 'Enceladus',    name: 'Enceladus (Male) - Steady' },
];

export default function Stage3Voices({ apiBase, blocks, videoFile, targetLang = 'hi-IN', experimentMode = 'translated_sarvam', sessionId, finalVideoUrl, onComplete, onViewResult }) {
  const [loading, setLoading] = useState(false);
  const [videoUrl, setVideoUrl] = useState(null);
  const [playingVideo, setPlayingVideo] = useState(false);
  const [previewLoading, setPreviewLoading] = useState({}); // { speakerId: boolean }
  const videoRef = useRef(null);
  const playTimeoutRef = useRef(null);

  useEffect(() => {
    if (videoFile) {
        const url = URL.createObjectURL(videoFile);
        setVideoUrl(url);
        return () => URL.revokeObjectURL(url);
    }
  }, [videoFile]);

  // Extract unique speakers and their first appearance timestamp
  const speakersData = useMemo(() => {
    const spks = {};
    blocks.forEach(b => {
        const s = b.speakers && b.speakers.length > 0 ? b.speakers[0] : 'S0';
        if (!spks[s]) {
            spks[s] = { id: s, startTime: b.timestamps[0] || 0 };
        }
    });
    return Object.values(spks);
  }, [blocks]);

  // Initial mapping (Fallback to different voices)
  const [voiceMap, setVoiceMap] = useState(() => {
    const initMap = {};
    speakersData.forEach((s) => {
        initMap[s.id] = 'auto';
    });
    return initMap;
  });

  const playContextSnippet = (startTime) => {
      if (videoRef.current) {
          clearTimeout(playTimeoutRef.current);
          setPlayingVideo(true);
          videoRef.current.currentTime = startTime;
          videoRef.current.play();

          // Stop playing after 4 seconds
          playTimeoutRef.current = setTimeout(() => {
              if (videoRef.current) {
                  videoRef.current.pause();
              }
              setPlayingVideo(false);
          }, 4000);
      }
  };

  const handleVoiceChange = (speaker, voiceId) => {
    setVoiceMap(prev => ({ ...prev, [speaker]: voiceId }));
  };

  const handlePreview = async (speakerId, voiceId) => {
    if (!voiceId || voiceId === '') return;
    setPreviewLoading(prev => ({ ...prev, [speakerId]: true }));
    try {
        const response = await axios.post(`${apiBase}/preview-voice`, {
            voice_id: voiceId,
            target_lang: targetLang
        }, { responseType: 'blob' });
        
        const audioUrl = URL.createObjectURL(response.data);
        const audio = new Audio(audioUrl);
        await audio.play();
    } catch (err) {
        console.error("Preview failed:", err);
        alert("Failed to play voice preview. Check console for details.");
    } finally {
        setPreviewLoading(prev => ({ ...prev, [speakerId]: false }));
    }
  };

  const handleSynthesize = async () => {
    if (!sessionId) {
        alert("Session tracking was lost (likely due to a browser reload). Please go back and upload the video again.");
        return;
    }
    setLoading(true);
    try {
      // Find exact duration in milliseconds if available
      const durationMs = videoRef.current ? videoRef.current.duration * 1000 : 0;
        
      const payload = {
          session_id: sessionId,
          transcript_blocks: blocks,
          voice_map: Object.keys(voiceMap).map(k => ({ speaker_id: k, voice_id: voiceMap[k] })),
          target_duration_ms: durationMs,
          target_lang: targetLang,
          auto_detect_speakers: true,
          disable_voice_cloning: false,
          synthesis_mode: 'gemini_tts',
      };
      
      const res = await axios.post(`${apiBase}/synthesize`, payload);
      if (res.data.warning) {
        alert(res.data.warning);
      }
      onComplete(res.data.audio_url, res.data.video_url);
    } catch (err) {
        console.error(err);
        const backendDetail = err?.response?.data?.detail;
        alert(`Synthesis failed.${backendDetail ? `\n\n${backendDetail}` : " Check backend logs for details."}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h2 style={{fontSize: '1.5rem', marginBottom: '1rem', textAlign: 'center'}}>Stage 3: Dub Generation</h2>
      <p style={{color: 'var(--text-muted)', marginBottom: '2rem', textAlign: 'center'}}>
        Generate the final dub.
      </p>
      <p style={{color: 'var(--text-muted)', marginBottom: '1rem', textAlign: 'center', fontSize: '0.9rem'}}>
        Active mode: <strong>{experimentMode.replaceAll('_', ' ')}</strong>
        {' '}(backend uses transcript + Gemini TTS on this branch)
      </p>

      {experimentMode === 'hindi_transcribed' && (
        <div
          style={{
            margin: '0 auto 1.25rem auto',
            maxWidth: '640px',
            padding: '0.85rem 1rem',
            borderRadius: '10px',
            border: '1px solid rgba(248, 113, 113, 0.45)',
            background: 'rgba(248, 113, 113, 0.1)',
            color: '#fecaca',
            fontSize: '0.9rem',
            lineHeight: 1.45,
            textAlign: 'left',
          }}
        >
          You are in <strong>raw STT</strong> mode: the lines below are what Stage 1 transcribed from the video
          (often <strong>English</strong> for an English clip), <strong>not</strong> the Hindi you produced in Stage 2.
          If that is wrong, go back and choose &quot;Sarvam: speak the translation you reviewed above&quot;.
        </div>
      )}


      <div style={{ background: 'var(--bg-card)', padding: '1.5rem', borderRadius: '12px', marginBottom: '2rem', border: '1px solid var(--border-light)' }}>
        <h3 style={{marginBottom: '1rem', color: 'var(--text-muted)'}}>
          {`Script for dub (${targetLang})`}
        </h3>
        <div style={{display: 'flex', flexDirection: 'column', gap: '1rem', maxHeight: '350px', overflowY: 'auto', paddingRight: '0.5rem'}}>
          {blocks.map((b, i) => (
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
                <div style={{
                    flex: 1, 
                    margin: 0, 
                    lineHeight: '1.5', 
                    background: 'rgba(0,0,0,0.3)', 
                    border: '1px solid var(--border-light)',
                    color: 'var(--text-light)',
                    padding: '0.75rem',
                    borderRadius: '8px',
                    minHeight: '40px'
                  }}>
                  {b.transcript}
                </div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1.5rem', marginBottom: '2rem'}}>
        {speakersData.map(spk => (
          <div key={spk.id} style={{background: 'rgba(0,0,0,0.2)', padding: '1rem', borderRadius: '12px', border: '1px solid var(--border-light)'}}>
             <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem'}}>
               <div style={{display: 'flex', alignItems: 'center', gap: '0.5rem'}}>
                 <Mic size={20} color="var(--primary)" />
                 <h3 style={{margin: 0}}>Speaker {spk.id}</h3>
               </div>
               
               <button 
                 onClick={() => playContextSnippet(spk.startTime)}
                 style={{background: 'rgba(59, 130, 246, 0.1)', border: '1px solid rgba(59, 130, 246, 0.3)', color: '#60a5fa', padding: '0.3rem 0.5rem', borderRadius: '4px', display: 'flex', gap: '0.3rem', alignItems: 'center', cursor: 'pointer'}}
                 title="Play 4-second interaction to check character context"
               >
                 <PlayCircle size={16} /> Reference
               </button>
             </div>
             
             <div className="form-group mb-0">
              <label>Assign Voice Model (Auto or Manual)</label>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <select 
                  value={voiceMap[spk.id] || ''} 
                  onChange={(e) => handleVoiceChange(spk.id, e.target.value)}
                  style={{background: 'rgba(15, 23, 42, 0.8)', flex: 1}}
                >
                  {AVAILABLE_VOICES.map(v => (
                     <option key={v.id} value={v.id}>{v.name}</option>
                  ))}
                </select>
                <button
                  onClick={() => handlePreview(spk.id, voiceMap[spk.id])}
                  disabled={previewLoading[spk.id]}
                  style={{
                    background: 'rgba(139, 92, 246, 0.1)',
                    border: '1px solid rgba(139, 92, 246, 0.3)',
                    color: '#a78bfa',
                    padding: '0 0.75rem',
                    borderRadius: '6px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer'
                  }}
                  title="Preview this Gemini voice speaking Hindi"
                >
                  {previewLoading[spk.id] ? (
                    <Loader2 size={16} className="loader" />
                  ) : (
                    <Volume2 size={16} />
                  )}
                </button>
              </div>
             </div>
          </div>
        ))}
      </div>

      {speakersData.length === 0 && (
         <div style={{textAlign: 'center', marginBottom: '2rem', padding: '2rem', border: '1px solid var(--border-light)', borderRadius: '12px'}}>
             No speakers detected. Creating single narrator track.
         </div>
      )}

      <div style={{textAlign: 'center', display: 'flex', justifyContent: 'center', gap: '1rem'}}>
        <button className="btn" onClick={handleSynthesize} disabled={loading} style={{background: 'linear-gradient(to right, #8b5cf6, #3b82f6)', minWidth: '200px'}}>
          {loading ? (
             <><Loader2 className="loader" size={20} /> Generating Gemini TTS dub...</>
          ) : (
             <><Wand2 size={20} /> Generate Dub (Gemini TTS)</>
          )}
        </button>

        {finalVideoUrl && !loading && (
          <button className="btn btn-secondary" onClick={onViewResult} style={{minWidth: '200px'}}>
             <Film size={20} /> View Download Screen
          </button>
        )}
      </div>

      {/* Floating Video Context Modal - Kept always mounted so the video engine doesn't destroy itself on UI toggle */}
      <div style={{
          display: playingVideo ? 'block' : 'none',
          position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: '400px', background: 'var(--bg-dark)', border: '1px solid var(--border-light)', borderRadius: '12px', padding: '1rem', boxShadow: '0 10px 50px rgba(0,0,0,0.8)', zIndex: 100
      }}>
          <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem'}}>
              <h4 style={{margin: 0, fontSize: '0.9rem', color: 'var(--text-muted)'}}>Context Viewer</h4>
              <button 
                  onClick={() => {
                      if (videoRef.current) videoRef.current.pause();
                      setPlayingVideo(false);
                      clearTimeout(playTimeoutRef.current);
                  }} 
                  style={{background: 'transparent', border: 'none', color: 'white', cursor: 'pointer'}}>
                  <X size={16} />
              </button>
          </div>
          {videoUrl && (
              <video 
                  ref={videoRef} 
                  src={videoUrl} 
                  style={{width: '100%', borderRadius: '8px', background: 'black'}} 
                  controls={true}
              />
          )}
      </div>
    </div>
  );
}
