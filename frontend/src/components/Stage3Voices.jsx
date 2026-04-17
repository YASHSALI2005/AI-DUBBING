import React, { useState, useMemo, useRef, useEffect } from 'react';
import axios from 'axios';
import { Mic, Loader2, Wand2, PlayCircle, X, Film } from 'lucide-react';

// Exact speakers supported by Sarvam bulbul:v3 (48kHz premium)
const AVAILABLE_VOICES = [
  // Female voices
  { id: 'ritu',     name: 'Ritu     (Female) - Warm' },
  { id: 'priya',    name: 'Priya    (Female) - Natural' },
  { id: 'neha',     name: 'Neha     (Female) - Bright' },
  { id: 'pooja',    name: 'Pooja    (Female) - Soft' },
  { id: 'simran',   name: 'Simran   (Female) - Clear' },
  { id: 'kavya',    name: 'Kavya    (Female) - Expressive' },
  { id: 'ishita',   name: 'Ishita   (Female) - Young' },
  { id: 'shreya',   name: 'Shreya   (Female) - Melodic' },
  { id: 'roopa',    name: 'Roopa    (Female) - Steady' },
  { id: 'tanya',    name: 'Tanya    (Female) - Professional' },
  { id: 'shruti',   name: 'Shruti   (Female) - Crisp' },
  { id: 'suhani',   name: 'Suhani   (Female) - Gentle' },
  { id: 'kavitha',  name: 'Kavitha  (Female) - Rich' },
  { id: 'rupali',   name: 'Rupali   (Female) - Soothing' },
  { id: 'niharika', name: 'Niharika (Female) - Dynamic' },
  { id: 'amelia',   name: 'Amelia   (Female) - Neutral' },
  { id: 'sophia',   name: 'Sophia   (Female) - Polished' },
  { id: 'mani',     name: 'Mani     (Female) - Lively' },
  // Male voices
  { id: 'aditya',   name: 'Aditya   (Male)   - Deep' },
  { id: 'ashutosh', name: 'Ashutosh (Male)   - Authoritative' },
  { id: 'rahul',    name: 'Rahul    (Male)   - Friendly' },
  { id: 'rohan',    name: 'Rohan    (Male)   - Natural' },
  { id: 'amit',     name: 'Amit     (Male)   - Strong' },
  { id: 'dev',      name: 'Dev      (Male)   - Cool' },
  { id: 'ratan',    name: 'Ratan    (Male)   - Mature' },
  { id: 'varun',    name: 'Varun    (Male)   - Energetic' },
  { id: 'manan',    name: 'Manan    (Male)   - Calm' },
  { id: 'sumit',    name: 'Sumit    (Male)   - Smooth' },
  { id: 'kabir',    name: 'Kabir    (Male)   - Bold' },
  { id: 'aayan',    name: 'Aayan    (Male)   - Young' },
  { id: 'shubh',    name: 'Shubh    (Male)   - Clear' },
  { id: 'advait',   name: 'Advait   (Male)   - Crisp' },
  { id: 'anand',    name: 'Anand    (Male)   - Warm' },
  { id: 'tarun',    name: 'Tarun    (Male)   - Confident' },
  { id: 'sunny',    name: 'Sunny    (Male)   - Bright' },
  { id: 'gokul',    name: 'Gokul    (Male)   - Rich' },
  { id: 'vijay',    name: 'Vijay    (Male)   - Full' },
  { id: 'mohit',    name: 'Mohit    (Male)   - Narrative' },
  { id: 'rehan',    name: 'Rehan    (Male)   - Smooth' },
  { id: 'soham',    name: 'Soham    (Male)   - Steady' },
];

export default function Stage3Voices({ apiBase, blocks, videoFile, targetLang = 'hi-IN', sessionId, finalVideoUrl, onComplete, onViewResult }) {
  const [loading, setLoading] = useState(false);
  const [videoUrl, setVideoUrl] = useState(null);
  const [playingVideo, setPlayingVideo] = useState(false);
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
    speakersData.forEach((s, idx) => {
        initMap[s.id] = AVAILABLE_VOICES[idx % AVAILABLE_VOICES.length].id;
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
          target_lang: targetLang
      };
      
      const res = await axios.post(`${apiBase}/synthesize`, payload);
      if (res.data.failed_block_count > 0) {
        alert(`Dub generated with ${res.data.failed_block_count} skipped block(s) due to API limits. Try again for a cleaner result.`);
      }
      onComplete(res.data.audio_url, res.data.video_url);
    } catch (err) {
        console.error(err);
        alert("Synthesis failed. Check backend.");
        // Demo fallback
        onComplete(`/temp/mock.wav`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h2 style={{fontSize: '1.5rem', marginBottom: '1rem', textAlign: 'center'}}>Stage 3: Voice Assignment & Sythesis</h2>
      <p style={{color: 'var(--text-muted)', marginBottom: '2rem', textAlign: 'center'}}>
        Assign a Sarvam AI voice to each detected speaker, then generate the final mixed dub.
      </p>
      
      <div style={{ background: 'var(--bg-card)', padding: '1.5rem', borderRadius: '12px', marginBottom: '2rem', border: '1px solid var(--border-light)' }}>
        <h3 style={{marginBottom: '1rem', color: 'var(--text-muted)'}}>Translated Text ({targetLang})</h3>
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
               <label>Assign Voice Model</label>
               <select 
                 value={voiceMap[spk.id] || ''} 
                 onChange={(e) => handleVoiceChange(spk.id, e.target.value)}
                 style={{background: 'rgba(15, 23, 42, 0.8)'}}
               >
                 {AVAILABLE_VOICES.map(v => (
                    <option key={v.id} value={v.id}>{v.name}</option>
                 ))}
               </select>
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
             <><Loader2 className="loader" size={20} /> Synthesizing & Mixing (.wav)</>
          ) : (
             <><Wand2 size={20} /> Generate AI Dub</>
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
