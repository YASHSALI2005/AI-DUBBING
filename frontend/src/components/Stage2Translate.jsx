import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Languages, Loader2, CheckCircle, ArrowLeft } from 'lucide-react';

// Only languages that Sarvam TTS (bulbul) supports
const TARGET_LANGUAGES = [
  { code: 'hi-IN', name: 'Hindi' },
  { code: 'bn-IN', name: 'Bengali' },
  { code: 'ta-IN', name: 'Tamil' },
  { code: 'te-IN', name: 'Telugu' },
  { code: 'mr-IN', name: 'Marathi' },
  { code: 'kn-IN', name: 'Kannada' },
  { code: 'ml-IN', name: 'Malayalam' },
  { code: 'gu-IN', name: 'Gujarati' },
  { code: 'pa-IN', name: 'Punjabi' },
  { code: 'od-IN', name: 'Odia' },
];

export default function Stage2Translate({ apiBase, blocks, sourceLang, onComplete }) {
  const [targetLang, setTargetLang] = useState('mr-IN');
  const [loading, setLoading] = useState(false);
  const [originalBlocks, setOriginalBlocks] = useState([]);
  const [translatedBlocks, setTranslatedBlocks] = useState([]);
  const [hasTranslated, setHasTranslated] = useState(false);

  useEffect(() => {
    if (blocks) {
      setOriginalBlocks([...blocks]);
    }
  }, [blocks]);

  const handleTextChange = (index, newText, isTranslated = false) => {
    if (isTranslated) {
      const updated = [...translatedBlocks];
      updated[index].transcript = newText;
      setTranslatedBlocks(updated);
    } else {
      const updated = [...originalBlocks];
      updated[index].transcript = newText;
      setOriginalBlocks(updated);
    }
  };

  const handleTranslate = async () => {
    setLoading(true);
    try {
      const res = await axios.post(`${apiBase}/translate`, {
        transcript_blocks: originalBlocks,
        target_lang: targetLang,
        source_lang: sourceLang
      });
      setTranslatedBlocks(res.data.blocks);
      if (res.data.failed_block_count > 0) {
        alert(`Translation completed with ${res.data.failed_block_count} fallback block(s). You can manually edit them below.`);
      }
      setHasTranslated(true);
    } catch (err) {
        console.error(err);
        alert("Translation failed. See console for details.");
    } finally {
      setLoading(false);
    }
  };

  const currentDisplayBlocks = hasTranslated ? translatedBlocks : originalBlocks;
  const sourceLangName = TARGET_LANGUAGES.find(l => l.code === sourceLang)?.name || 'Original';
  const targetLangName = TARGET_LANGUAGES.find(l => l.code === targetLang)?.name || 'Target';

  return (
    <div>
      <h2 style={{fontSize: '1.5rem', marginBottom: '1rem', textAlign: 'center'}}>
        {hasTranslated ? `Review ${targetLangName} Translation` : `Select Translation Language`}
      </h2>
      
      {!hasTranslated ? (
        <p style={{color: 'var(--text-muted)', marginBottom: '2rem', textAlign: 'center'}}>
            The audio has been successfully transcribed. Please select the target language to continue.
        </p>
      ) : (
        <p style={{color: 'var(--text-muted)', marginBottom: '2rem', textAlign: 'center'}}>
            Review and refine the {targetLangName.toLowerCase()} translation below.
        </p>
      )}

      {hasTranslated && (
        <div style={{ background: 'var(--bg-card)', padding: '1.5rem', borderRadius: '12px', marginBottom: '2rem', border: '1px solid var(--border-light)' }}>
          <h3 style={{marginBottom: '1rem', color: 'var(--text-muted)'}}>
              {targetLangName} Translation Results
          </h3>
          <div style={{display: 'flex', flexDirection: 'column', gap: '1rem', maxHeight: '400px', overflowY: 'auto', paddingRight: '0.5rem'}}>
            {translatedBlocks.map((b, i) => (
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
                  <textarea 
                    value={b.transcript}
                    onChange={(e) => handleTextChange(i, e.target.value, true)}
                    style={{
                      flex: 1, 
                      margin: 0, 
                      lineHeight: '1.5', 
                      background: 'rgba(0,0,0,0.3)', 
                      border: '1px solid var(--border-light)',
                      color: 'var(--text-light)',
                      padding: '0.75rem',
                      borderRadius: '8px',
                      resize: 'vertical',
                      minHeight: '60px'
                    }}
                  />
              </div>
            ))}
          </div>
        </div>
      )}

      {!hasTranslated ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1.5rem' }}>
            <div className="form-group" style={{width: '100%', maxWidth: '400px'}}>
                <label>Translate Video To:</label>
                <select value={targetLang} onChange={(e) => setTargetLang(e.target.value)}>
                {TARGET_LANGUAGES.map(l => (
                    <option key={l.code} value={l.code}>{l.name}</option>
                ))}
                </select>
            </div>

            <button className="btn" onClick={handleTranslate} disabled={loading} style={{ background: 'var(--accent)', minWidth: '250px' }}>
                {loading ? (
                    <><Loader2 className="loader" size={20} /> Translating...</>
                ) : (
                    <><Languages size={20} /> Translate to {TARGET_LANGUAGES.find(l => l.code === targetLang)?.name}</>
                )}
            </button>
        </div>
      ) : (
        <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center' }}>
            <button className="btn btn-secondary" onClick={() => setHasTranslated(false)}>
                <ArrowLeft size={20} /> Change Language
            </button>
            <button className="btn" onClick={() => onComplete(translatedBlocks, targetLang)} style={{ background: 'var(--primary)' }}>
                <CheckCircle size={20} /> Confirm & Proceed to Voices
            </button>
        </div>
      )}
    </div>
  );
}
