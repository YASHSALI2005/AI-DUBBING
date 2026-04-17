import React from 'react';
import { PlayCircle, Download, RotateCcw, Film, ArrowLeft } from 'lucide-react';

export default function AudioPlayer({ audioUrl, videoUrl, onBack, onReset }) {
  return (
    <div className="text-center" style={{padding: '2rem 0'}}>
      <div style={{marginBottom: '1rem', display: 'flex', justifyContent: 'center'}}>
        {videoUrl ? <Film size={64} color="var(--primary)" /> : <PlayCircle size={64} color="var(--primary)" />}
      </div>
      
      <h2 style={{fontSize: '2rem', marginBottom: '1rem', background: 'linear-gradient(to right, #4ade80, #3b82f6)', WebkitBackgroundClip: 'text', color: 'transparent'}}>
        Dubbing Complete!
      </h2>
      
      <p style={{color: 'var(--text-muted)', marginBottom: '2rem'}}>
        Your multiphonics dubbed file has been successfully generated and mixed.
      </p>

      <div style={{background: 'rgba(0,0,0,0.3)', padding: '2rem', borderRadius: '16px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1.5rem', marginBottom: '2rem'}}>
         {videoUrl ? (
           <video controls style={{width: '100%', maxWidth: '600px', borderRadius: '8px', background: 'black'}}>
               <source src={videoUrl} type="video/mp4" />
               Your browser does not support the video element.
           </video>
         ) : (
           <audio controls style={{width: '100%', maxWidth: '500px'}}>
               <source src={audioUrl} type="audio/wav" />
               Your browser does not support the audio element.
           </audio>
         )}
         
         <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', justifyContent: 'center' }}>
           <a 
             href={audioUrl} 
             download="final_dubbed_mix.wav"
             target="_blank"
             rel="noopener noreferrer"
             className="btn"
             style={{textDecoration: 'none', background: 'var(--primary)'}}
           >
             <Download size={20} /> Download isolated .WAV Audio
           </a>

           {videoUrl && (
             <a 
               href={videoUrl} 
               download="final_dubbed_video.mp4"
               target="_blank"
               rel="noopener noreferrer"
               className="btn"
               style={{textDecoration: 'none', background: 'var(--accent)'}}
             >
               <Download size={20} /> Download Final .MP4 Video
             </a>
           )}
         </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '3rem', borderTop: '1px solid var(--border-light)', paddingTop: '2rem' }}>
        <button className="btn btn-secondary" onClick={onReset}>
           <RotateCcw size={20} /> Start New Project
        </button>
        
        <button className="btn" onClick={onBack} style={{ background: 'var(--primary)' }}>
           <ArrowLeft size={20} /> Back / Modify Voices
        </button>
      </div>
    </div>
  );
}
