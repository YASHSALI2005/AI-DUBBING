import React, { useState, useEffect } from 'react';
import { LogOut } from 'lucide-react';
import Stage1Upload from './components/Stage1Upload';
import Stage2Translate from './components/Stage2Translate';
import Stage3Voices from './components/Stage3Voices';
import AudioPlayer from './components/AudioPlayer';
import Login from './components/Login';
import DirectDub from './components/DirectDub';

const API_BASE = 'http://localhost:8000/api';
const APP_STATE_KEY = 'vrfilms_dubbing_state_v1';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(() => {
    return localStorage.getItem('vrfilms_auth_state_v1') === 'true';
  });
  const [appMode, setAppMode] = useState('pipeline'); // 'pipeline' | 'direct'
  const [currentStage, setCurrentStage] = useState(1);
  const [transcriptBlocks, setTranscriptBlocks] = useState([]);
  const [translatedBlocks, setTranslatedBlocks] = useState([]);
  const [finalAudioUrl, setFinalAudioUrl] = useState(null);
  const [videoFile, setVideoFile] = useState(null);
  const [selectedLang, setSelectedLang] = useState('hi-IN');
  const [sourceLang, setSourceLang] = useState('hi-IN');
  const [sessionId, setSessionId] = useState(null);
  const [finalVideoUrl, setFinalVideoUrl] = useState(null);
  const [experimentMode, setExperimentMode] = useState('translated_sarvam');

  useEffect(() => {
    try {
      const stageFromHash = Number((window.location.hash || '').replace('#stage', ''));
      const raw = window.localStorage.getItem(APP_STATE_KEY);
      if (!raw) {
        if ([1, 2, 3, 4].includes(stageFromHash)) {
          setCurrentStage(stageFromHash);
        }
        return;
      }

      const saved = JSON.parse(raw);
      setTranscriptBlocks(Array.isArray(saved.transcriptBlocks) ? saved.transcriptBlocks : []);
      setTranslatedBlocks(Array.isArray(saved.translatedBlocks) ? saved.translatedBlocks : []);
      setFinalAudioUrl(saved.finalAudioUrl || null);
      setSelectedLang(saved.selectedLang || 'hi-IN');
      setSourceLang(saved.sourceLang || 'hi-IN');
      setSessionId(saved.sessionId || null);
      setFinalVideoUrl(saved.finalVideoUrl || null);
      setExperimentMode(saved.experimentMode || 'translated_sarvam');

      const persistedStage = Number(saved.currentStage) || 1;
      const stage = [1, 2, 3, 4].includes(stageFromHash) ? stageFromHash : persistedStage;
      setCurrentStage(stage);
      window.history.replaceState({ stage }, `Stage ${stage}`, `#stage${stage}`);
    } catch (err) {
      console.warn('Could not restore persisted app state.', err);
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        APP_STATE_KEY,
        JSON.stringify({
          currentStage,
          transcriptBlocks,
          translatedBlocks,
          finalAudioUrl,
          selectedLang,
          sourceLang,
          sessionId,
          finalVideoUrl,
          experimentMode,
        })
      );
    } catch (err) {
      console.warn('Could not persist app state.', err);
    }
  }, [
    currentStage,
    transcriptBlocks,
    translatedBlocks,
    finalAudioUrl,
    selectedLang,
    sourceLang,
    sessionId,
    finalVideoUrl,
    experimentMode,
  ]);

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

  // Fast-upload path: file landed via /api/upload-fast (no STT). Skip Stage 2
  // entirely and jump straight to Stage 3 in Gemini per-segment dub mode —
  // Gemini will transcribe + diarize + translate + dub during synthesis.
  const handleFastUploaded = (uploadedFile, sId, tgtLang) => {
    setVideoFile(uploadedFile);
    setSessionId(sId);
    setTranscriptBlocks([]);
    setTranslatedBlocks([]);
    setSourceLang('auto');
    setSelectedLang(tgtLang || 'hi-IN');
    setExperimentMode('gemini_segment_dub');
    navigateTo(3);
  };

  const handleTranslated = (blocks, lang, expMode) => {
    setTranslatedBlocks(blocks);
    if (lang) setSelectedLang(lang);
    if (expMode) setExperimentMode(expMode);
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
    setTranscriptBlocks([]);
    setTranslatedBlocks([]);
    setExperimentMode('translated_sarvam');
    try {
      window.localStorage.removeItem(APP_STATE_KEY);
    } catch (err) {
      console.warn('Could not clear persisted app state.', err);
    }
    window.history.pushState({ stage: 1 }, "Stage 1", "#stage1");
    setCurrentStage(1);
  };

  const handleLogin = () => {
    setIsAuthenticated(true);
    localStorage.setItem('vrfilms_auth_state_v1', 'true');
  };

  const handleLogout = () => {
    setIsAuthenticated(false);
    localStorage.removeItem('vrfilms_auth_state_v1');
  };

  if (!isAuthenticated) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className="app-container">
      <header className="header" style={{ position: 'relative' }}>
        <button 
          onClick={handleLogout}
          className="btn btn-secondary" 
          style={{ position: 'absolute', right: 0, top: 0, padding: '0.5rem 1rem' }}
          title="Sign Out"
        >
          <LogOut size={18} />
          <span style={{ marginLeft: '0.5rem' }}>Sign Out</span>
        </button>
        <h1>PARROT AI Dubbing </h1>
        <p>Automated Video Localization Pipeline</p>

      </header>

      {appMode === 'pipeline' && (
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
      )}

      <main className="glass-card">
        {appMode === 'direct' && (
          <DirectDub apiBase={API_BASE} />
        )}

        {appMode === 'pipeline' && currentStage === 1 && (
          <Stage1Upload
            apiBase={API_BASE}
            onComplete={handleTranscribed}
            onFastUploadComplete={handleFastUploaded}
          />
        )}
        
        {appMode === 'pipeline' && currentStage === 2 && (
          <Stage2Translate 
            apiBase={API_BASE} 
            blocks={transcriptBlocks} 
            sourceLang={sourceLang}
            sessionId={sessionId}
            videoFile={videoFile}
            onComplete={handleTranslated} 
          />
        )}

        {appMode === 'pipeline' && currentStage === 3 && (
          <Stage3Voices 
            apiBase={API_BASE} 
            blocks={translatedBlocks} 
            videoFile={videoFile}
            targetLang={selectedLang}
            experimentMode={experimentMode}
            sessionId={sessionId}
            finalVideoUrl={finalVideoUrl}
            onComplete={handleSynthesized}
            onViewResult={() => navigateTo(4)}
          />
        )}

        {appMode === 'pipeline' && currentStage === 4 && (
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
