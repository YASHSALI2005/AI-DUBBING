import React, { useState, useEffect } from 'react';
import Stage1Upload from './components/Stage1Upload';
import Stage2Translate from './components/Stage2Translate';
import Stage3Voices from './components/Stage3Voices';
import AudioPlayer from './components/AudioPlayer';

const API_BASE = 'http://localhost:8000/api';

function App() {
  const [currentStage, setCurrentStage] = useState(1);
  const [transcriptBlocks, setTranscriptBlocks] = useState([]);
  const [translatedBlocks, setTranslatedBlocks] = useState([]);
  const [finalAudioUrl, setFinalAudioUrl] = useState(null);
  const [videoFile, setVideoFile] = useState(null);
  const [selectedLang, setSelectedLang] = useState('hi-IN');
  const [sourceLang, setSourceLang] = useState('hi-IN');
  const [sessionId, setSessionId] = useState(null);
  const [finalVideoUrl, setFinalVideoUrl] = useState(null);

  // Sync state with browser history so "Back" works
  useEffect(() => {
    const handlePopState = (event) => {
      if (event.state && event.state.stage) {
        setCurrentStage(event.state.stage);
      } else {
        setCurrentStage(1);
      }
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // When stage changes, push to history if it's not a popstate
  const navigateTo = (stage) => {
    if (stage !== currentStage) {
      window.history.pushState({ stage }, `Stage ${stage}`, `#stage${stage}`);
      setCurrentStage(stage);
    }
  };

  const handleTranscribed = (blocks, uploadedFile, sId, srcLang) => {
    setTranscriptBlocks(blocks);
    setVideoFile(uploadedFile);
    setSessionId(sId);
    setSourceLang(srcLang || 'hi-IN');
    navigateTo(2);
  };

  const handleTranslated = (blocks, lang) => {
    setTranslatedBlocks(blocks);
    if (lang) setSelectedLang(lang);
    navigateTo(3);
  };

  const handleSynthesized = (audioUrl, videoUrl) => {
    setFinalAudioUrl(audioUrl ? `http://localhost:8000${audioUrl}` : null);
    setFinalVideoUrl(videoUrl ? `http://localhost:8000${videoUrl}` : null);
    navigateTo(4);
  };

  const handleReset = () => {
    setSessionId(null);
    setFinalAudioUrl(null);
    setFinalVideoUrl(null);
    setVideoFile(null);
    window.history.pushState({ stage: 1 }, "Stage 1", "#stage1");
    setCurrentStage(1);
  };

  return (
    <div className="app-container">
      <header className="header">
        <h1>VR FILMS AI Dubbing Tool</h1>
        <p>Automated Video Localization Pipeline</p>
      </header>

      <div className="stepper">
        {[1, 2, 3].map(step => (
          <div 
            key={step} 
            className={`step ${currentStage === step ? 'active' : ''} ${currentStage > step ? 'completed' : ''}`}
          >
            {currentStage > step ? '✓' : step}
          </div>
        ))}
      </div>

      <main className="glass-card">
        {currentStage === 1 && (
          <Stage1Upload apiBase={API_BASE} onComplete={handleTranscribed} />
        )}
        
        {currentStage === 2 && (
          <Stage2Translate 
            apiBase={API_BASE} 
            blocks={transcriptBlocks} 
            sourceLang={sourceLang}
            onComplete={handleTranslated} 
          />
        )}

        {currentStage === 3 && (
          <Stage3Voices 
            apiBase={API_BASE} 
            blocks={translatedBlocks} 
            videoFile={videoFile}
            targetLang={selectedLang}
            sessionId={sessionId}
            finalVideoUrl={finalVideoUrl}
            onComplete={handleSynthesized}
            onViewResult={() => navigateTo(4)}
          />
        )}

        {currentStage === 4 && (
          <AudioPlayer 
            audioUrl={finalAudioUrl} 
            videoUrl={finalVideoUrl}
            onBack={() => navigateTo(3)}
            onReset={handleReset} 
          />
        )}
      </main>
    </div>
  );
}

export default App;
