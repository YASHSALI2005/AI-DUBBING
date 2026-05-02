import React, { useState, useEffect } from 'react';
import Stage1Upload from './components/Stage1Upload';
import Stage2Translate from './components/Stage2Translate';
import Stage3Voices from './components/Stage3Voices';
import AudioPlayer from './components/AudioPlayer';
import Login from './components/Login';
import DirectDub from './components/DirectDub';
import ProfileMenu from './components/ProfileMenu';
import Settings from './components/Settings';
import { API_BASE, API_ORIGIN } from './apiConfig';
const APP_STATE_KEY  = 'vrfilms_dubbing_state_v1';
const AUTH_TOKEN_KEY = 'vrfilms_auth_token_v2';
const AUTH_USER_KEY  = 'vrfilms_auth_user_v2';

function App() {
  const [authToken, setAuthToken] = useState(() => localStorage.getItem(AUTH_TOKEN_KEY) || null);
  const [authUser, setAuthUser]   = useState(() => {
    try { return JSON.parse(localStorage.getItem(AUTH_USER_KEY) || 'null'); } catch { return null; }
  });
  const [view, setView]           = useState('app'); // 'app' | 'settings'
  const [appMode, setAppMode]     = useState('pipeline'); // 'pipeline' | 'direct'
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

  const isAuthenticated = !!authToken && !!authUser;
  const isAdmin         = authUser?.role === 'admin';

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
    setFinalAudioUrl(audioUrl ? `${API_ORIGIN}${audioUrl}` : null);
    setFinalVideoUrl(videoUrl ? `${API_ORIGIN}${videoUrl}` : null);
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

  const handleLogin = (token, user) => {
    setAuthToken(token);
    setAuthUser(user);
    localStorage.setItem(AUTH_TOKEN_KEY, token);
    localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
    // Clean up legacy auth flag from earlier versions
    localStorage.removeItem('vrfilms_auth_state_v1');
  };

  const handleLogout = () => {
    setAuthToken(null);
    setAuthUser(null);
    setView('app');
    localStorage.removeItem(AUTH_TOKEN_KEY);
    localStorage.removeItem(AUTH_USER_KEY);
  };

  const openSettings = () => {
    if (!isAdmin) return;
    setView('settings');
  };

  if (!isAuthenticated) {
    return <Login apiBase={API_BASE} onLogin={handleLogin} />;
  }

  return (
    <div className="app-container">
      <header className="header" style={{ position: 'relative' }}>
        <div style={{ position: 'absolute', right: 0, top: 0 }}>
          <ProfileMenu
            user={authUser}
            onOpenSettings={openSettings}
            onLogout={handleLogout}
          />
        </div>
        <h1>PARROT AI Dubbing </h1>
        <p>Automated Video Localization Pipeline</p>
      </header>

      {view === 'settings' && isAdmin && (
        <main className="glass-card">
          <Settings
            apiBase={API_BASE}
            token={authToken}
            currentUser={authUser}
            onBack={() => setView('app')}
          />
        </main>
      )}

      {view === 'app' && (
        <>
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
        </>
      )}
    </div>
  );
}

export default App;
