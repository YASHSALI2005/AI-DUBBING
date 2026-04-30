#!/usr/bin/env python3
"""
TTS Tone/Emotion Matching Experiment  --  Gemini 3.1 TTS Preview
=================================================================
Usage:
  python tts_experiment.py <input_file> [options]

Supports any number of speakers:
  - 1 or 2 speakers  -> Gemini's native MultiSpeakerVoiceConfig (gemini-multi)
  - 3+ speakers      -> per-segment single-speaker TTS stitched onto the
                        original timeline by timestamp (single-per-segment)
  - 'auto' picks the right mode based on speaker count.

Examples:
  python tts_experiment.py clip.mp4
  python tts_experiment.py clip.mp4 --target-lang hi-IN
  python tts_experiment.py clip.mp4 --model gemini-2.5-pro
  python tts_experiment.py clip.mp4 --analysis-only
  python tts_experiment.py clip.mp4 --skip-analysis --cached-analysis prev.json
  python tts_experiment.py clip.mp4 --multi-speaker-mode single-per-segment
"""

import argparse
import io
import json
import mimetypes
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── config ────────────────────────────────────────────────────────────────────
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

TTS_MODEL = "gemini-3.1-flash-tts-preview"
# Speed-match limits for dubbing mode.
# Max tempo: anything above 1.25x sounds like "2x" — keep it natural.
# Min tempo: below 0.80x sounds robotic/dragged — pad silence instead.
DUBBING_MAX_TEMPO = float(os.getenv("DUBBING_MAX_TEMPO", "1.25"))
DUBBING_MIN_TEMPO = float(os.getenv("DUBBING_MIN_TEMPO", "0.82"))

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v"}

VOICE_GENDER = {
    "Puck": "male", "Charon": "male", "Fenrir": "male", "Orus": "male",
    "Enceladus": "male", "Iapetus": "male", "Umbriel": "male",
    "Algenib": "male", "Algieba": "male", "Schedar": "male",
    "Achird": "male", "Zubenelgenubi": "male", "Sadachbia": "male",
    "Sadaltager": "male", "Alnilam": "male", "Rasalgethi": "male",
    "Zephyr": "female", "Kore": "female", "Leda": "female", "Aoede": "female",
    "Callirrhoe": "female", "Autonoe": "female", "Despina": "female",
    "Erinome": "female", "Laomedeia": "female", "Achernar": "female",
    "Gacrux": "female", "Pulcherrima": "female", "Vindemiatrix": "female",
    "Sulafat": "female",
}

VOICE_CATALOG_TEXT = """
MALE VOICES (with character archetypes they fit best):

  Algenib       - Gravelly baritone. Grizzled, weathered older man.
                  FITS: seasoned detective, hardened DA, cynical veteran,
                  mafia boss, war-worn soldier, gritty antihero.

  Orus          - Firm, formal, clear baritone. Corporate/professional.
                  FITS: CEO giving speech, military commander, doctor,
                  formal announcer, measured authority figure.

  Charon        - Informative, polished, neutral narrator.
                  FITS: documentary narrator, news anchor, audiobook reader,
                  educational explainer, calm presenter.

  Iapetus       - Clear, crisp, articulate mid-range.
                  FITS: tech presenter, young professional, clean narrator,
                  confident speaker, prosecutor cross-examining.

  Enceladus     - Breathy, intimate, slightly raspy.
                  FITS: storyteller, mysterious character, late-night radio,
                  close-whisper scenes, suspense/noir.

  Fenrir        - Excitable, high-energy male.
                  FITS: sports commentator, hype man, excited host,
                  enthusiastic teenager.

  Puck          - Upbeat, cheerful, youthful.
                  FITS: friendly sidekick, cheerful host, optimistic kid,
                  commercial voice-over.

  Umbriel       - Easy-going, laid-back mid-range.
                  FITS: chill friend, casual narrator, relaxed podcaster.

  Algieba       - Smooth, silky, menacing-capable.
                  FITS: charming villain, seductive antagonist, silky host,
                  mob lawyer, cool negotiator.

  Schedar       - Even-toned, neutral, measured.
                  FITS: therapist, neutral moderator, formal but soft.

  Achird        - Friendly, warm male.
                  FITS: kind uncle, supportive friend, trustworthy guide.

  Zubenelgenubi - Casual, conversational mid-range.
                  FITS: everyday guy, buddy, casual dialogue, sidekick.

  Sadachbia     - Lively, animated.
                  FITS: energetic speaker, entertainer, dynamic pitch.

  Sadaltager    - Knowledgeable, confident authority.
                  FITS: expert witness, professor, seasoned lawyer,
                  confident interviewee.

  Alnilam       - Firm, grounded, deep.
                  FITS: father figure, stern authority, military.

  Rasalgethi    - Informative, articulate, slightly deep.
                  FITS: professional host, analyst, expert commentator.

FEMALE VOICES (with character archetypes):

  Zephyr        - Bright, clear, upbeat.
                  FITS: cheerful host, friendly receptionist, young professional.

  Kore          - Firm, confident, mid-range.
                  FITS: executive, strict teacher, confident lead, lawyer.

  Leda          - Youthful, light, innocent.
                  FITS: teenage girl, young daughter, bubbly friend.

  Aoede         - Breezy, relaxed.
                  FITS: chill narrator, casual friend, laid-back host.

  Callirrhoe    - Easy-going, natural.
                  FITS: conversational host, relaxed podcaster, friendly guide.

  Autonoe       - Bright, clear, crisp.
                  FITS: news anchor, confident presenter, spokesperson.

  Despina       - Smooth, velvety, can be sultry.
                  FITS: femme fatale, seductive character, smooth host,
                  mysterious narrator.

  Erinome       - Clear, articulate.
                  FITS: professional explainer, teacher, tech presenter.

  Laomedeia     - Upbeat, energetic.
                  FITS: enthusiastic host, cheerful announcer.

  Achernar      - Soft, gentle.
                  FITS: kind mother, lullaby singer, soothing voice.

  Gacrux        - Mature, rich, slightly deep female.
                  FITS: wise grandmother, authoritative matriarch,
                  serious anchor, stern but fair.

  Pulcherrima   - Forward, bold, attention-grabbing.
                  FITS: dynamic speaker, confident lead, bold advocate.

  Vindemiatrix  - Gentle, warm mid-range.
                  FITS: caring teacher, supportive friend, therapist.

  Sulafat       - Warm, rich, friendly.
                  FITS: radio host, warm narrator, welcoming presenter.
"""

AUDIO_TAGS_REFERENCE = """
IMPORTANT: Gemini TTS tags are NOT a closed list. From the official docs:
  "There is no exhaustive list on what tags do and don't work, we recommend
   experimenting with different emotions and expressions."
You may use ANY descriptive bracketed phrase. Multi-word freeform tags
like [like a tired detective at the end of a long shift] or
[sarcastically, one painfully slow word at a time] are FULLY SUPPORTED and
often produce more nuanced delivery than single-word tags.
For non-English transcripts (Hindi, etc.), KEEP ALL TAGS IN ENGLISH.

==========================================================================
CRITICAL TAG SELECTION RULES
==========================================================================
1. FREEFORM TAGS > SINGLE-WORD TAGS. A freeform tag like
   [like a college friend teasing casually] beats [sarcastic] every time
   because it gives the model character + emotion + context at once.
2. EACH LINE NEEDS A TAG. Every sentence must have at least one tag.
   Never leave a line untagged — the TTS model uses tags as its only
   acting direction.
3. NO REDUNDANT TAGS. Don't stack tags that say the same thing.
   BAD: [fast] [quickly] — pick the one that's more specific.
4. TAG SCOPE: Tags apply to the sentence/phrase that follows them.
   Repeat a persistent tag on each new sentence — it does NOT carry over.
5. NON-SPEECH TAGS ONLY WHEN AUDIBLE. Only add [laughs], [sigh], [gasp],
   [cough], [uhm] if you can clearly hear that sound in the source audio.
   In dramatic/formal scenes these are immersion-breaking — omit them.

==========================================================================
NON-SPEECH SOUND TAGS  (only when audible in source)
==========================================================================
  [sigh]           - audible exhale / frustration breath
  [laughs]         - outright laugh
  [chuckles]       - soft, quiet laugh
  [giggles]        - light, high laugh (often female / teen)
  [scoffs]         - dismissive exhale-laugh
  [clears throat]  - throat-clearing before speaking
  [uhm]            - filled pause / hesitation
  [gasp]           - sharp intake of breath (shock/fear)
  [cough]          - audible cough

==========================================================================
EMOTION / DELIVERY MODIFIERS  (change how the speech sounds)
==========================================================================
CONVERSATIONAL / CASUAL:
  [warmly]              - friendly, open, welcoming
  [casually]            - relaxed, off-hand, like talking to a friend
  [playfully]           - light, fun, teasing without malice
  [cheerfully]          - upbeat, positive energy
  [excitedly]           - high enthusiasm, elevated pitch
  [enthusiastically]    - energetic, engaged
  [brightly]            - clear, upbeat, alert

NEGATIVE / TENSE:
  [annoyed]             - mild frustration, clipped delivery
  [irritably]           - sharper irritation
  [frustrated]          - contained anger, tight jaw
  [indignantly]         - offended, righteously upset
  [angrily]             - open anger, louder, clipped
  [curtly]              - short, dismissive, no warmth
  [dismissively]        - brushing off, not taking seriously
  [defensively]         - guarding, quick to justify
  [sarcastically]       - mocking tone, often stretched vowels
  [bitterly]            - edge of resentment in voice
  [coldly]              - flat affect, emotionally withdrawn

VULNERABLE / EMOTIONAL:
  [wearily]             - drained, tired, worn down
  [sadly]               - subdued, slow, downcast
  [reluctantly]         - hesitant, unwilling, dragging feet
  [nervously]           - slightly higher pitch, faster pace
  [anxiously]           - worried, breathless
  [trembling]           - shaky, unstable voice (fear/grief)
  [crying]              - tearful, voice breaks
  [as if about to cry]  - holding back tears
  [softly]              - low volume, gentle

FORMAL / AUTHORITATIVE:
  [seriously]           - grave, no-nonsense
  [firmly]              - assertive, no room for argument
  [confidently]         - assured, no hesitation
  [authoritatively]     - commanding, expects compliance
  [measured]            - controlled, deliberate pace
  [solemnly]            - weighty, ceremonial gravity

SURPRISE / WONDER:
  [surprised]           - sudden pitch shift up
  [shocked]             - stronger than surprised, breathless
  [amazed]              - awed, slightly speechless
  [disbelievingly]      - "I can't believe this" tone
  [in disbelief]        - same as above, slightly softer

SINISTER / MENACING:
  [menacingly]          - quiet threat, controlled
  [ominously]           - dark foreboding
  [smugly]              - self-satisfied, slightly superior

PHYSICAL STATE:
  [breathlessly]        - out of breath, rushed
  [sleepily]            - drowsy, trailing off
  [drunkenly]           - slurred, loose

==========================================================================
FREEFORM DESCRIPTIVE TAGS  (PREFERRED — always beats single-word tags)
==========================================================================
Pattern: [like X] or [as if Y] or [adjective, adjective, like Z]

CASUAL / SOCIAL:
  [like a college friend teasing you about a bad decision]
  [like someone venting to their best friend]
  [like someone who just got roasted and is fighting back]
  [warm and conversational, like talking to a close friend]
  [like a sibling who's heard this excuse a hundred times]
  [like someone confidently telling a story at a party]
  [playfully teasing, with a grin in the voice]

PROFESSIONAL / FORMAL:
  [like a lawyer asking a serious question in cross-examination]
  [like a prosecutor pressing a reluctant witness]
  [like a tired old detective at the end of a long shift]
  [like a news anchor delivering breaking news]
  [like a teacher explaining patiently to a child]
  [like a doctor giving difficult news calmly]
  [like a CEO addressing a room of shareholders]

DRAMATIC / EMOTIONAL:
  [as if barely holding back tears]
  [as if the weight of the world is in these words]
  [like someone confessing something for the first time]
  [like someone who's given up fighting but is still angry]
  [as if reading from a resignation letter]

ANTAGONISTIC / MENACING:
  [like a mafia boss making a quiet, final offer]
  [like a villain who's already won and knows it]
  [sarcastically, one painfully slow word at a time]
  [like someone who finds your pain mildly amusing]
  [cold and clipped, like hanging up a phone call]

Mix freeform + simple tags for layered effect:
  [annoyed] [like a sibling who's heard this excuse a hundred times]
  [seriously] [like a lawyer asking a serious question in cross-examination]

==========================================================================
PACE MODIFIERS
==========================================================================
  [very fast]       - rushing, stumbling over words
  [fast]            - quicker than natural, urgent
  [slow]            - deliberate, weighted
  [very slow]       - dramatically drawn out
  [extremely fast]  - legal-disclaimer speed

==========================================================================
PAUSES
==========================================================================
  [short pause]   - ~250ms  (comma-level beat)
  [medium pause]  - ~500ms  (end-of-thought beat)
  [long pause]    - ~1000ms (dramatic or loaded silence)

==========================================================================
WHISPER / VOLUME
==========================================================================
  [whispers]                    - hushed, intimate
  [whispering intensely]        - urgent whisper, high stakes
  [shouting]                    - projected, loud
  [under their breath]          - quiet, muttered, barely audible
  [softly, close to the mic]    - intimate, almost private
"""

TAGGING_EXAMPLES = """
==========================================================================
GENRE: CASUAL / FRIEND GROUP  (most common real-world case)
==========================================================================
Example 1 - Friend group banter / teasing:
  Speaker1: [curtly] Pass karo mujhe.
  Speaker2: [chuckles] [wearily, like someone who's heard this a hundred times]
             Main bhi pass. Tum log toh mujhe loot loge.
  Speaker3: [playfully teasing, with a grin in the voice]
             Tu? Africa? Tu toh baal frizz hone pe hysterical ho jaata hai.
  Speaker1: [annoyed] [fast] Mere baal control nahi hote, okay?
  -- NOTE: In casual friend-group clips prefer [playfully teasing], [warmly],
     [casually], [like a sibling who's heard this excuse before] over blunt
     single-word tags like [sarcastic] or [excited]. These give more natural TTS.

Example 2 - Defensive comeback after being roasted:
  "[annoyed] [like someone who just got roasted and is fighting back]
   Yaar, that's not fair. [short pause] [defensively] I was literally ready.
   [fast] You don't know what happened."

Example 3 - Sharing an ambitious plan to skeptical friends:
  "[confidently, like someone telling a story at a party]
   Main Africa jaaunga, NGO ke saath kaam karunga.
   [short pause] [firmly] Seriously, I've thought about this."

Example 4 - Mediator de-escalating a petty argument:
  "[measured] [like a friend trying to de-escalate a petty argument]
   Okay okay, chill. [short pause] [casually] Game pe focus karte hain."

==========================================================================
GENRE: DRAMATIC / FORMAL
==========================================================================
Example 5 - Cross-examination:
  Speaker1: [seriously] [like a lawyer asking a serious question in cross-examination]
             What I'm asking you is [short pause] how many times have you
             testified against a fellow inmate?
  Speaker2: [reluctantly] This makes my fourth.
  Speaker1: [seriously] [like a lawyer asking a serious question in cross-examination]
             Your fourth time. [short pause] [slow] Four times
             [short pause] you've testified for the prosecution.
  -- NOTE: Repeat the freeform tag on EVERY line of the same speaker.

Example 6 - Emotional confrontation:
  "[as if the weight of the world is in these words] [slow]
   I gave everything I had [short pause] to this company.
   [medium pause] [trembling] And you just threw it all away.
   [long pause] [angrily] How could you do this?!"

Example 7 - Quiet confession:
  "[softly] [as if barely holding back tears]
   I never told anyone this. [medium pause]
   [whispering] I was scared. [long pause]
   [measured] But I think you need to know."

==========================================================================
GENRE: TENSE / THRILLER / ACTION
==========================================================================
Example 8 - Urgent warning:
  "[breathlessly] [fast] They're coming, we have to go NOW.
   [short pause] [whispering intensely] Don't make a sound."

Example 9 - Menacing villain:
  "[coldly] [like a mafia boss making a quiet, final offer]
   I'm only going to say this once. [long pause]
   [menacingly] Think very carefully about your next move."

==========================================================================
GENRE: INFORMATIONAL / DOCUMENTARY
==========================================================================
Example 10 - Narrator:
  "[like a documentary narrator] In 1969, humanity reached the moon.
   [short pause] [measured] What followed was fifty years of questions
   about what comes next."

==========================================================================
PERSISTENCE RULES  (ALL genres)
==========================================================================
  WRONG:  [annoyed] First line. Second line. Third line.
  RIGHT:  [annoyed] First line. [annoyed] Second line. [annoyed] Third line.

  If dominant emotion persists — TAG EVERY SENTENCE.
  Only change or drop a tag when emotion AUDIBLY shifts.
"""

# ── Genre → tag style mapping ─────────────────────────────────────────────────
# This is injected into the analysis prompt so the model picks tag style
# based on what it actually detects in the audio, not hardcoded assumptions.

GENRE_TAG_STYLE = """\
==========================================================================
GENRE → TAG STYLE GUIDE
==========================================================================
After you detect the genre from the audio, use the matching tag style below.
These are GUIDELINES — always override with what you actually hear.

GENRE: casual_friends
  Scene: friends hanging out, chatting, joking, arguing casually
  Prefer: [warmly], [casually], [playfully], [annoyedly], [excitedly],
          [like a friend teasing you about a bad decision],
          [like someone venting to their best friend],
          [like a sibling who's heard this excuse a hundred times],
          [playfully teasing, with a grin in the voice],
          [like someone confidently telling a story at a party]
  Avoid:  [seriously], [firmly], [solemnly], [authoritatively],
          courtroom/professional freeform tags, heavy dramatic pauses

GENRE: family_conversation
  Scene: parents, siblings, relatives talking — affectionate or tense
  Prefer: [warmly], [firmly], [wearily], [with quiet concern],
          [like a parent scolding a child they still love],
          [like someone trying to keep the peace at dinner],
          [like an older sibling laying down the rules]
  Avoid:  over-formal tags, courtroom language

GENRE: workplace_professional
  Scene: office, meeting, negotiation, interview, pitch
  Prefer: [confidently], [measured], [firmly], [enthusiastically],
          [like a CEO addressing a room of shareholders],
          [like someone pitching an idea they believe in],
          [like a manager giving difficult feedback calmly]
  Avoid:  casual/slang freeform tags, heavy emotional tags

GENRE: courtroom_legal
  Scene: trial, deposition, interrogation, legal argument
  Prefer: [seriously], [firmly], [slow],
          [like a lawyer asking a serious question in cross-examination],
          [like a prosecutor pressing a reluctant witness],
          [like a defendant trying to stay composed under pressure]
  Avoid:  casual tags, laughs, playful freeform tags

GENRE: news_documentary
  Scene: news broadcast, documentary narration, explainer
  Prefer: [like a documentary narrator], [authoritatively], [measured],
          [like a news anchor delivering breaking news],
          neutral pace, clear delivery, minimal emotional tags
  Avoid:  emotional tags, casual freeform tags, non-speech sounds

GENRE: emotional_drama
  Scene: breakup, confession, argument, grief, apology
  Prefer: [trembling], [softly], [as if barely holding back tears],
          [as if the weight of the world is in these words],
          [bitterly], [reluctantly], [angrily], [slow]
  Avoid:  upbeat tags, casual freeform tags

GENRE: action_thriller
  Scene: chase, confrontation, danger, high stakes
  Prefer: [breathlessly], [fast], [panicked], [whispering intensely],
          [like someone who just escaped something terrifying],
          [coldly], [menacingly], [like a mafia boss making a quiet final offer]
  Avoid:  slow/dramatic pauses (unless tense silence), casual tags

GENRE: romantic_intimate
  Scene: confession of feelings, tender moment, flirting
  Prefer: [softly], [warmly], [whispering], [softly close to the mic],
          [breathlessly], [nervously], [like someone saying I love you for the first time]
  Avoid:  formal/authoritative tags, dramatic/heavy tags

GENRE: comedy_banter
  Scene: stand-up, sketch, roast, playful back-and-forth
  Prefer: [playfully], [sarcastically], [with a smirk in the voice],
          [like someone delivering a punchline they're proud of],
          [like someone who finds this whole situation ridiculous],
          [chuckles], [giggles] (only if audible)
  Avoid:  serious/solemn tags unless used ironically

GENRE: motivational_speech
  Scene: keynote, pep talk, commencement speech, rally
  Prefer: [enthusiastically], [firmly], [confidently], [slow] for emphasis,
          [like a coach rallying the team before a big game],
          [like a speaker who truly believes every word]
  Avoid:  casual tags, hesitant tags

GENRE: horror_suspense
  Scene: fear, dread, something wrong, supernatural
  Prefer: [trembling], [whispers], [scared], [as if afraid of being overheard],
          [like someone who knows something is very wrong],
          [ominously], [under their breath]
  Avoid:  cheerful tags, fast-paced tags (unless fleeing)

GENRE: other
  Scene: anything that doesn't fit above
  Rule:   Describe the scene in 1 sentence, then pick tags that match
          the EMOTIONAL REGISTER you actually hear — warm/cold, fast/slow,
          formal/casual, tense/relaxed. Always prefer a specific freeform
          tag over a generic single-word tag.
"""

# ── Analysis prompt ───────────────────────────────────────────────────────────

ANALYSIS_PROMPT = f"""Listen to this audio clip carefully. Determine how many DISTINCT speakers
are present. There is NO maximum — could be 1, 2, 3, 4 or more.

The TTS model will receive ONLY your tagged transcript (nothing else), so the
tags are the ONLY way to convey how each part should sound.

CRITICAL: You MUST also segment the audio by speaker turns and provide per-segment
TIMESTAMPS (in seconds, floats). Each contiguous turn from one speaker is one
segment. These timestamps are how we re-assemble the dub onto the original
video timeline, so they must reflect when each speaker actually starts/ends.

==========================================================================
STEP 0 — DETECT GENRE FIRST  (do this before anything else)
==========================================================================
Before transcribing or tagging, listen to the whole clip and identify:

1. SCENE TYPE — what kind of scene is this?
   Pick the single best match from this list:
     casual_friends, family_conversation, workplace_professional,
     courtroom_legal, news_documentary, emotional_drama, action_thriller,
     romantic_intimate, comedy_banter, motivational_speech,
     horror_suspense, other

2. SCENE DESCRIPTION — one sentence describing what's happening.
   Example: "Four college friends arguing about their future plans over a game."

3. REGISTER — overall tone of the clip:
   Pick one: formal | semi-formal | casual | intimate | comedic | tense | mixed

These three fields go into the JSON output (genre, scene_description, register).
They also determine WHICH tag style you use — see GENRE → TAG STYLE GUIDE below.

IMPORTANT: Once you identify the genre, your tags must come from that genre's
style. Do NOT use courtroom/legal tags for a casual friend conversation.
Do NOT use casual/playful tags for a news broadcast. The genre you detect
is your acting direction — it overrides any default assumptions.

==========================================================================
GENRE → TAG STYLE GUIDE  (use the style matching your detected genre)
==========================================================================
{GENRE_TAG_STYLE}

==========================================================================
AUDIO TAG REFERENCE (full vocabulary — pick from here based on genre)
==========================================================================
Per the official Gemini TTS docs, "there is no exhaustive list" of tags.
Use the common tags below WHEN they fit, but PREFER richer freeform
descriptive tags whenever they better capture the character or delivery.
Multi-word freeform tags often produce more nuanced delivery than single-word
tags, and have been shown to give the best results in practice.
For non-English transcripts (Hindi, etc.), KEEP ALL TAGS IN ENGLISH.

{AUDIO_TAGS_REFERENCE}

==========================================================================
TAGGING EXAMPLES (genre-aware — notice how tag style changes per genre)
==========================================================================
{TAGGING_EXAMPLES}

==========================================================================
VOICE CATALOG (pick the best voice for each speaker)
==========================================================================
{VOICE_CATALOG_TEXT}

==========================================================================
CHARACTER ARCHETYPE (THINK CAREFULLY — CRITICAL FOR VOICE CASTING)
==========================================================================
For each speaker, identify the CHARACTER ARCHETYPE they represent.
This is the single most important decision for voice casting.

Common archetypes:
  - Seasoned interrogator / DA / detective   -> gravelly, authoritative (e.g. Algenib)
  - Formal news anchor / narrator            -> clear, polished, neutral (e.g. Charon)
  - Charming villain / silky antagonist      -> smooth, menacing (e.g. Algieba)
  - Nervous young defendant / suspect        -> unsure, slightly high
  - Wise elder / mentor                      -> deep, measured
  - Energetic sports commentator / host      -> fast, excited, bright (e.g. Fenrir)
  - Tired weary protagonist                  -> low energy, breathy
  - Angry boss / confrontational figure      -> firm, sharp, deep (e.g. Alnilam)
  - Irate / frustrated customer              -> lively but tense/aggressive
  - Warm mother / kind teacher               -> soft, gentle
  - Mafia boss / hardened criminal           -> gravelly, cold
  - Seductive / femme fatale                 -> smooth, velvety
  - Young adult / college student            -> light, natural, energetic
  - Casual friend / buddy                    -> relaxed, warm, conversational

For EACH speaker:
  1. Listen to VOICE QUALITIES: pitch, depth, timbre, roughness.
  2. Listen to EMOTIONAL DELIVERY: cold? warm? angry? tired? urgent?
  3. Consider SCENE CONTEXT from the genre you detected.
  4. Infer likely GENDER from audible voice cues first, then validate with
     linguistic/context clues in transcript (self-reference, address terms, etc.).
     If cues conflict, trust what is clearly audible in the voice.
  5. Combine these -> identify the ARCHETYPE.
  6. Match the archetype to the voice catalog's FITS descriptions.

GENDER CONSISTENCY (IMPORTANT):
- The "gender" field must match the speaker's audible voice presentation.
- Do not assign male/female from text alone when audio indicates otherwise.
- Before finalizing, run a consistency check across transcript + segments +
  tagged_transcript so each Speaker's gender, archetype, style_direction, and
  recommended_voice remain aligned.
- If uncertain, choose the best audible fit and keep it consistent everywhere.

Do NOT just pick based on one-word tags. Pick based on character fit AND
emotional valence. Do NOT use cheerful voices (e.g. Fenrir) for angry people.
Do NOT use gravelly authoritative voices for casual college-age friends.

STYLE DIRECTION: write a short (1-2 sentence) style direction describing
the CHARACTER in the context of THIS scene. Example for a casual clip:
  "A college student, slightly defensive when friends tease them,
   but fundamentally warm and part of the group."

==========================================================================
YOUR TASK
==========================================================================

1. Detect genre (Step 0 above) — fill genre, scene_description, register.
2. Transcribe the audio VERBATIM.
3. Identify ALL distinct speakers (no maximum).
4. Split into SEGMENTS — one per continuous speaker turn.
   Each segment: speaker, start, end, text (no tags), tagged_text.
5. Insert audio tags using the GENRE TAG STYLE you detected.
   Rules:
   a. FREEFORM TAGS > single-word tags. Always prefer a specific descriptive
      tag over a generic one when the scene gives you enough context.
   b. TAG EVERY SENTENCE — never leave a line untagged.
   c. NON-SPEECH SOUNDS ([laughs], [sigh], [gasp] etc.) ONLY if clearly
      audible in the source. Never invent them as decoration.
   d. TONE PERSISTENCE: if a speaker's emotion holds across sentences,
      repeat the tag on EVERY sentence. Don't drop it after the first line.
      WRONG:   [annoyedly] First line. Second line.
      CORRECT: [annoyedly] First line. [annoyedly] Second line.
   e. PRECISION: avoid vague tags when a freeform option fits better.
      WORSE:  [assertive]
      BETTER: [like a lawyer asking a serious question in cross-examination]
6. Produce a "tagged_transcript" joining all segments in order.
   Multi-speaker: prefix each line with "SpeakerName: ".
   Single-speaker: no prefix.

Return a JSON object with EXACTLY these fields:
{{
  "genre": "<one of: casual_friends | family_conversation | workplace_professional | courtroom_legal | news_documentary | emotional_drama | action_thriller | romantic_intimate | comedy_banter | motivational_speech | horror_suspense | other>",
  "scene_description": "<one sentence describing the scene>",
  "register": "<formal | semi-formal | casual | intimate | comedic | tense | mixed>",
  "transcript": "<verbatim transcript WITHOUT any tags>",
  "num_speakers": <integer>,
  "speakers": [
    {{
      "name": "<e.g. Speaker1>",
      "gender": "male" or "female",
      "character_archetype": "<archetype label>",
      "voice_reasoning": "<1 sentence: why this voice fits>",
      "recommended_voice": "<voice name from catalog>",
      "style_direction": "<1-2 sentence character description>"
    }}
  ],
  "segments": [
    {{
      "speaker": "Speaker1",
      "start": 0.0,
      "end": 4.5,
      "text": "<verbatim text, no tags>",
      "tagged_text": "<text with inline audio tags matching detected genre>"
    }}
  ],
  "tagged_transcript": "<full tagged transcript, SpeakerName: prefix if multi-speaker>",
  "language": "<BCP-47 code>",
  "pace": "<very fast | fast | moderate-fast | moderate | moderate-slow | slow | very slow>"
}}

SCHEMA RULES:
- "speakers" must have exactly "num_speakers" entries.
- "segments" must be chronological (start-time ascending).
- Every segment's "speaker" must match a name in "speakers".
- Timestamps are floats in seconds from clip start.
- Each speaker's "gender" must be consistent with audible voice cues and with
  that speaker's archetype/style/voice choice.
- genre/scene_description/register must reflect what you ACTUALLY heard,
  not a default assumption.

Return ONLY the JSON. No markdown fences, no commentary.
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _resolve_tool(name: str) -> str:
    """Return the full path to an ffmpeg/ffprobe binary.

    shutil.which searches PATH, including WinGet/AppData locations that
    subprocess may not find when launched from inside a venv on Windows.
    Falls back to the bare name so subprocess can still try.
    """
    return shutil.which(name) or name


FFMPEG  = _resolve_tool("ffmpeg")
FFPROBE = _resolve_tool("ffprobe")


def _has_ffmpeg() -> bool:
    try:
        subprocess.run([FFMPEG, "-version"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def dub_tighten_tags(tagged: str) -> str:
    """Aggressively tighten tagged transcript for dubbing.

    Hindi/Hinglish syllables are denser than English, so TTS already runs
    long. To keep the dub close to source duration, this strips:
      - ALL pause tags ([short/medium/long/very long pause])
      - All slow-pace tags ([slow], [very slow], [extremely slow])
    The character/tone tags (e.g. [serious], [like a lawyer ...]) are kept.
    """
    import re
    before = tagged

    tagged = re.sub(r"\[(very\s+|extremely\s+)?slow\]\s*",     "", tagged, flags=re.IGNORECASE)
    tagged = re.sub(r"\[(short|medium|long|very\s+long)\s+pause\]\s*", "", tagged, flags=re.IGNORECASE)
    tagged = re.sub(r"\s{2,}", " ", tagged).strip()

    if tagged != before:
        print("[dub-tighten] stripped all pauses + slow tags to keep dub timing tight")
    return tagged


def get_audio_duration(path: Path) -> float:
    """Get duration in seconds using ffprobe."""
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def speed_match_wav(wav_path: Path, target_duration_s: float,
                    max_speed: float = 1.25,
                    min_speed: float = 0.85,
                    tolerance_ratio: float = 0.01,
                    segment_dur_s: float | None = None) -> None:
    """Time-stretch a wav to match target duration (pitch-preserving).

    Uses ffmpeg's `rubberband` filter — higher quality than `atempo` for
    speech. Caps speed adjustments to stay within the threshold where
    speed-up/slow-down starts sounding unnatural.

    segment_dur_s: when provided (per-segment dubbing mode), the max_speed
    cap is tightened further for SHORT segments because a 1.3x stretch on a
    1-second clip is far more noticeable than on a 5-second clip.
    Scaling rule:
      seg < 1.5s  -> cap at min(max_speed, 1.15)
      seg < 2.5s  -> cap at min(max_speed, 1.20)
      seg >= 2.5s -> use max_speed as-is
    """
    current = get_audio_duration(wav_path)
    if current <= 0 or target_duration_s <= 0:
        return
    ratio = current / target_duration_s
    if (1.0 - tolerance_ratio) <= ratio <= (1.0 + tolerance_ratio):
        print(f"[speed-match] already close ({current:.1f}s vs {target_duration_s:.1f}s), skipping")
        return

    # Tighten cap for short segments — artefacts are more audible there.
    effective_max = max_speed
    if segment_dur_s is not None:
        if segment_dur_s < 1.5:
            effective_max = min(max_speed, 1.15)
        elif segment_dur_s < 2.5:
            effective_max = min(max_speed, 1.20)

    # Bound the tempo so it doesn't get ridiculously fast or slow
    applied = max(min_speed, min(ratio, effective_max))

    if ratio > effective_max:
        print(f"[speed-match] WARN: would need {ratio:.2f}x but capping at "
              f"{effective_max:.2f}x (seg={segment_dur_s:.1f}s) to preserve natural sound. "
              f"Output will be {current / effective_max:.1f}s instead of {target_duration_s:.1f}s.")
    elif ratio < min_speed:
        print(f"[speed-match] WARN: would need {ratio:.2f}x but capping at "
              f"{min_speed:.2f}x to preserve natural sound. Output will be "
              f"{current / min_speed:.1f}s instead of {target_duration_s:.1f}s.")

    tmp = wav_path.with_suffix(".speedadj.wav")
    subprocess.run(
        [FFMPEG, "-y", "-i", str(wav_path),
         "-filter:a", f"rubberband=tempo={applied:.4f}",
         str(tmp)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    tmp.replace(wav_path)
    new_dur = get_audio_duration(wav_path)
    print(f"[speed-match] {current:.1f}s -> {new_dur:.1f}s "
          f"(target {target_duration_s:.1f}s, rubberband tempo={applied:.3f})")


def force_exact_duration(wav_path: Path, target_duration_s: float,
                         fade_out_ms: int = 80,
                         max_safe_trim_s: float = 0.8) -> None:
    """Make wav_path EXACTLY target_duration_s long — SAFELY.

    - If shorter: pad the end with silence so dubbed audio doesn't end early.
    - If slightly longer: trim with a short fade-out (residual cleanup).
    - If far longer (> max_safe_trim_s): REFUSE to silently chop dialogue.
      Print a clear warning and skip the trim, so the user notices that
      the upstream stages (translation/TTS pace) need tuning rather than
      having entire sentences silently disappear.
    """
    current = get_audio_duration(wav_path)
    if current <= 0 or target_duration_s <= 0:
        return
    delta = target_duration_s - current
    if abs(delta) < 0.05:
        print(f"[exact-dur] within 50ms ({current:.2f}s vs {target_duration_s:.2f}s), skipping")
        return

    tmp = wav_path.with_suffix(".exact.wav")
    if delta > 0:
        pad_ms = int(delta * 1000)
        af = f"apad=pad_dur={pad_ms}ms"
        print(f"[exact-dur] padding +{delta:.2f}s of silence to hit {target_duration_s:.2f}s")
    else:
        overshoot = -delta
        if overshoot > max_safe_trim_s:
            print(f"[exact-dur] WARN: WAV is {overshoot:.2f}s longer than target — "
                  f"that's more than the {max_safe_trim_s:.2f}s safety threshold. "
                  f"Refusing to silently cut dialogue. The translation/TTS likely "
                  f"produced too much audio. Suggested fixes: shorter translation, "
                  f"raise --max-speed-match (currently capped during speed-match), "
                  f"or use --no-exact-duration if you accept a longer output.")
            return
        af = (f"atrim=end={target_duration_s:.4f},"
              f"afade=t=out:st={max(0.0, target_duration_s - fade_out_ms / 1000):.4f}:"
              f"d={fade_out_ms / 1000:.4f}")
        print(f"[exact-dur] trimming {overshoot:.2f}s with {fade_out_ms}ms fade-out")

    subprocess.run(
        [FFMPEG, "-y", "-i", str(wav_path),
         "-af", af,
         str(tmp)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    tmp.replace(wav_path)


def extract_audio_to_wav(input_path: Path, output_wav: Path,
                         max_duration_s: float = 15.0) -> None:
    print(f"[extract] {input_path.name} -> {output_wav.name} "
          f"(max {max_duration_s:.0f}s) ...")
    subprocess.run(
        [FFMPEG, "-y", "-i", str(input_path),
         "-t", str(max_duration_s),
         "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", str(output_wav)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )


def mux_audio_into_video(video_path: Path, wav_path: Path,
                         output_mp4: Path) -> None:
    """Replace the original video's audio track with the TTS wav.

    - Copies video stream (no re-encode).
    - Encodes audio to AAC so it plays in browsers / most players.
    - Uses '-shortest' so the muxed output ends with whichever stream is shorter.
    """
    print(f"[mux] {video_path.name} + {wav_path.name} -> {output_mp4.name} ...")
    subprocess.run(
        [FFMPEG, "-y",
         "-i", str(video_path),
         "-i", str(wav_path),
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k",
         "-shortest",
         str(output_mp4)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )


def pcm_bytes_to_wav(pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
    n_ch, bps = 1, 16
    byte_rate   = sample_rate * n_ch * bps // 8
    block_align = n_ch * bps // 8
    data_len    = len(pcm_bytes)
    buf = bytearray()
    buf += b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
    buf += b"fmt " + struct.pack("<IHHIIHH", 16, 1, n_ch, sample_rate,
                                  byte_rate, block_align, bps)
    buf += b"data" + struct.pack("<I", data_len) + pcm_bytes
    return bytes(buf)


def detect_mime(path: Path) -> str:
    mapping = {
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".aac": "audio/aac",
        ".ogg": "audio/ogg", ".flac": "audio/flac", ".m4a": "audio/mp4",
        ".mp4": "video/mp4", ".mkv": "video/x-matroska",
        ".mov": "video/quicktime", ".avi": "video/avi", ".webm": "video/webm",
    }
    return mapping.get(path.suffix.lower(),
                       mimetypes.guess_type(str(path))[0] or "application/octet-stream")


def _call_with_retry(fn, label: str, max_attempts: int = 8, base_wait: float = 5.0):
    from google.genai import errors as genai_errors
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except (genai_errors.ServerError, genai_errors.ClientError) as exc:
            code = getattr(exc, "code", None) or getattr(exc, "status_code", 0)
            if code not in (429, 503):
                raise
            last_exc = exc
            wait = min(base_wait * (2 ** (attempt - 1)), 60.0)
            jitter = random.uniform(0, wait * 0.3)
            total = wait + jitter
            print(f"[retry] {label}: attempt {attempt}/{max_attempts}, "
                  f"HTTP {code} - waiting {total:.0f}s ...")
            time.sleep(total)
    raise last_exc


# ── step 3 : analyse audio -> tagged transcript ──────────────────────────────

def analyse_audio(client: genai.Client, audio_path: Path, model: str) -> dict:
    print(f"[analyse] sending to {model} ...")
    raw_bytes = audio_path.read_bytes()
    mime = detect_mime(audio_path)

    def _call():
        return client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=raw_bytes, mime_type=mime),
                ANALYSIS_PROMPT,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

    response = _call_with_retry(_call, "analyse_audio")
    raw = response.text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print("[analyse] WARNING: non-JSON response:\n", raw)
        raise RuntimeError(f"Gemini returned non-JSON: {exc}") from exc


# ── step 4 (optional) : translate tagged transcript ──────────────────────────

LANG_NAMES = {
    "hi-IN": "Hindi", "hi": "Hindi",
    "en-IN": "English", "en-US": "English", "en": "English",
    "ta-IN": "Tamil", "te-IN": "Telugu", "kn-IN": "Kannada",
    "ml-IN": "Malayalam", "bn-IN": "Bengali", "gu-IN": "Gujarati",
    "mr-IN": "Marathi", "pa-IN": "Punjabi",
}


def analysis_language_bucket(lang_code: str | None) -> str:
    """Map language code to analysis folder name."""
    if not lang_code:
        return "english"
    normalized = lang_code.lower()
    if normalized.startswith("hi"):
        return "hindi"
    return "english"


def bucket_from_analysis_filename(filename: str) -> str:
    """Best-effort language bucket from analysis filename."""
    lowered = filename.lower()
    if "_hi" in lowered or "hindi" in lowered:
        return "hindi"
    return "english"


def reorganize_existing_analysis_files(analysis_dir: Path) -> None:
    """Move top-level analysis JSON files into language subfolders."""
    for path in analysis_dir.glob("*.json"):
        bucket = bucket_from_analysis_filename(path.name)
        target = analysis_dir / bucket / path.name
        if path.resolve() == target.resolve():
            continue
        path.replace(target)
        print(f"[organize] moved -> {target}")


def translate_tagged_transcript(client: genai.Client, tagged_transcript: str,
                                target_lang: str, model: str,
                                is_multi: bool = False,
                                target_duration_s: float | None = None) -> str:
    import re
    target_name = LANG_NAMES.get(target_lang, target_lang)
    print(f"[translate] -> {target_name} using {model} ...")

    plain = re.sub(r"\[[^\]]+\]", "", tagged_transcript)
    if is_multi:
        plain = re.sub(r"(?m)^\s*Speaker\d+\s*:\s*", "", plain)
    src_word_count = len(plain.split())

    multi_note = ""
    if is_multi:
        multi_note = """
IMPORTANT: The transcript has speaker labels like "Speaker1:" and "Speaker2:".
Keep ALL speaker labels exactly as-is. Only translate the dialogue text after
the colon. Example:
  Original:  Speaker1: [serious] What are you doing here?
  Translated: Speaker1: [serious] Tum yahan kya kar rahe ho?
"""

    duration_note = ""
    if target_duration_s is not None:
        duration_note = (
            f"\nDURATION TARGET: This translation will be dubbed to fit "
            f"approximately {target_duration_s:.1f} seconds of video. "
            f"Be concise so TTS timing can match.\n"
        )

    prompt = f"""Translate this tagged transcript for dubbing into how a
modern urban {target_name} speaker ACTUALLY talks in real life.

TAGGED TRANSCRIPT:
{tagged_transcript}

LENGTH CONSTRAINT (CRITICAL for dubbing — read this VERY carefully):
  The source has ~{src_word_count} words (ignoring tags/labels).
  TARGET WORD COUNT: aim for {max(1, int(src_word_count * 0.55))} words
  (about 55% of source). HARD CEILING: you MUST NOT exceed
  {max(1, int(src_word_count * 0.62))} words under ANY circumstances.
  If you exceed this word count, the audio will fail to sync and sound rushed.
  You MUST aggressively cut non-essential words to hit this limit.
  Drop ALL redundant fillers ("you know", "sort of", "ek", "wo", "aise", "matlab",
  "toh", "haan", "aur", "lekin" when not needed for meaning).
  Why: {target_name} syllables take roughly 1.4-1.6x as long to speak as
  English syllables AND Hindi TTS models naturally speak slower than English.
  A direct 85%-word-count translation will ALWAYS run 1.4-1.6x over time budget.
  You must pre-compensate by targeting only 55% of source word count.
  CUT MERCILESSLY — keep only the core meaning of each line.
{duration_note}

================================================================
CRITICAL RULE FOR HINDI (and other Indian languages):
================================================================
This is NOT a formal / literary / "shudh" {target_name} translation.
Think CASUAL STREET HINGLISH — like how a Mumbai lawyer, Delhi
journalist, or a young professional would actually speak in real life.

DO NOT use textbook/pure Hindi vocabulary. BANNED EXAMPLES:

  BANNED (shudh Hindi)      ->  USE INSTEAD (Hinglish)
  ---------------------------------------------------
  "mukhbiri"                ->  "snitch" (keep English)
  "gawahi dena"             ->  "testify" (keep English)
  "abhiyojan"               ->  "prosecution" (keep English)
  "pratishthit"             ->  "popular" (keep English)
  "apradhi"                 ->  "criminal" (keep English)
  "kaidi"                   ->  "inmate" (keep English)
  "nyayalay"                ->  "court" (keep English)
  "saathi"                  ->  "fellow" (keep English) or just drop
  "aavashyak"               ->  "need" or "zaroori"
  "prashn"                  ->  "question" (keep English)

THE RULE: if a word is in COMMON ENGLISH-IN-HINDI use in India,
KEEP IT IN ENGLISH. Don't translate it to formal Hindi.

Think Netflix India / Amazon Prime Hindi dubbing — that level of
Hinglish. Or how Karan Johar / Zoya Akhtar film dialogues sound.

GOOD translation examples:
  "What I'm asking you is how many times have you snitched on a fellow inmate?"
  GOOD: "Main ye pooch raha hoon, kitni baar tumne ek fellow inmate pe snitch kiya hai?"
  BAD:  "Main ye pooch raha hoon, kitni baar tumne saathi kaidi ki mukhbiri ki?"

  "Four times you've testified for the prosecution."
  GOOD: "Chaar baar tumne prosecution ke liye testify kiya hai."
  BAD:  "Chaar baar tumne abhiyojan ke liye gawahi di hai."

  "Makes you a popular man."
  GOOD: "Tumhe ek popular aadmi banata hai." (or just "Popular aadmi ho tum.")
  BAD:  "Tumhe ek pratishthit purush banata hai."

OTHER RULES:
1. Keep ALL English nouns, technical terms, verbs, adjectives that are
   commonly used in Indian English — as-is in English.
2. Only translate the glue words (pronouns, basic verbs, conjunctions,
   question words, postpositions) to casual {target_name}.
3. Keep ALL audio tags exactly as-is in English. This includes BOTH
   simple tags ([sigh], [serious], [short pause], [fast], [sarcastic]) AND
   multi-word freeform descriptive tags like
   [like a lawyer asking serious question],
   [like a lawyer asking a serious question in cross-examination] or
   [as if barely holding back tears].
   Do NOT translate, paraphrase, shorten, remove, merge, or add tags.
   Copy every bracketed phrase byte-for-byte from the source.
4. Keep translation VERY CONCISE — target 55% of the original word count.
   This is the single most important rule. Hindi TTS runs long. Cut hard.
5. Keep the SAME casual register as the original. If original sounds
   intimidating, translation should sound intimidating in Hinglish.
{multi_note}
Return a JSON object with exactly one field:
{{
  "tagged_transcript": "<the casual Hinglish {target_name} tagged transcript>"
}}
"""

    def _call():
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

    response = _call_with_retry(_call, "translate")
    raw = response.text.strip()
    try:
        result = json.loads(raw)
        return result["tagged_transcript"]
    except (json.JSONDecodeError, KeyError) as exc:
        print("[translate] WARNING: bad response:\n", raw)
        raise RuntimeError(f"Translation failed: {exc}") from exc


# ── N-speaker single-TTS helpers ─────────────────────────────────────────────

VROTT_MARKER = "--- VROTT ---"
BATCH_SPLIT_PAUSE_MS = 1200


def resolve_voice_for_speaker(spk: dict) -> str:
    """Pick a Gemini TTS voice for one speaker dict from analysis['speakers']."""
    voice = spk.get("recommended_voice", "")
    if voice in VOICE_GENDER:
        return voice
    return "Charon" if spk.get("gender") == "male" else "Aoede"


def voice_map_from_analysis(analysis: dict) -> dict:
    """Map speaker name -> resolved voice name."""
    return {
        s["name"]: resolve_voice_for_speaker(s)
        for s in analysis.get("speakers", [])
        if s.get("name")
    }


def build_speaker_plan(analysis: dict) -> dict:
    """Group segments by speaker and produce a VROTT-separated text plan.

    Pure logging / auditing aid. The actual TTS input per segment is just
    that segment's tagged_text — VROTT is NEVER sent to the model and never
    appears in the synthesized audio. It exists only so a human reading
    `analysis.json` or the console output can visually see the boundaries
    between consecutive segments by the same speaker.
    """
    by_speaker: dict = {}
    for seg in analysis.get("segments", []):
        by_speaker.setdefault(seg.get("speaker", "Speaker?"), []).append(seg)

    plan: dict = {}
    for speaker_name, segs in by_speaker.items():
        # Sort by start time (defensive — input is already chronological).
        segs_sorted = sorted(segs, key=lambda s: float(s.get("start", 0.0) or 0.0))
        lines = []
        for i, seg in enumerate(segs_sorted):
            if i > 0:
                lines.append(VROTT_MARKER)
            start = float(seg.get("start", 0.0) or 0.0)
            end = float(seg.get("end", 0.0) or 0.0)
            tagged = seg.get("tagged_text") or seg.get("text", "")
            lines.append(f"[{start:.2f}-{end:.2f}] {tagged}")
        plan[speaker_name] = "\n".join(lines)
    return plan


def render_speaker_plan_text(plan: dict) -> str:
    """Pretty multi-line string of the speaker plan for console / log output."""
    out = []
    for speaker_name, content in plan.items():
        out.append(f"{speaker_name}:")
        for line in content.splitlines():
            out.append(f"  {line}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def build_segment_tts_input(seg: dict, speaker_profile: dict | None,
                            pace_label: str, is_dubbing: bool,
                            genre: str = "", scene_description: str = "",
                            source_language: str = "") -> str:
    """Build the single-speaker TTS input for one segment.

    Mirrors the Director's-Notes preamble used in main()'s single-speaker
    path but scoped to this segment's specific speaker.
    genre and scene_description (from analysis) are injected so TTS knows
    the scene context and picks the right delivery register.
    """
    pace_tag = ""
    if is_dubbing:
        pacing_note = (
            "### Pacing: natural conversational speed.\n"
            "* DO NOT add any pauses beyond what's explicitly tagged.\n"
            "* DO NOT linger on words or add dramatic breaths.\n"
            "* Keep gaps between sentences to a natural minimum.\n"
            "* First understand the scene and dialogue-delivery timing before speaking.\n"
            "* Keep the target dubbed language clear, but let delivery carry subtle source-language accent influence from the original audio.\n"
            "* If the source language is naturally fast (for example Spanish), keep energetic turn-taking and cadence while staying intelligible in the dubbed language.\n"
            "* After translation, align each character's speed to scene timing and emotional delivery.\n"
            "* If a sentence is long, increase speed as needed while keeping speech clear and natural.\n"
            "* If a sentence is very short, either add a gentle trailing '...' to extend delivery or adjust speed to fit naturally.\n"
            "* This is a dub fitting a fixed video duration - efficient delivery is key."
        )
    else:
        pacing_note = f"### Pacing: {pace_label}, natural rhythm, no rushing."
        if pace_label in ("fast", "very fast", "moderate-fast"):
            pace_tag = "[fast] "
        elif pace_label in ("slow", "very slow", "moderate-slow"):
            pace_tag = "[slow] "

    tagged = seg.get("tagged_text") or seg.get("text", "")

    # Build scene context note from detected genre
    scene_note = ""
    if genre:
        scene_note = f"### Scene type: {genre}"
        if scene_description:
            scene_note += f" — {scene_description}"
        scene_note += "\n"
    if source_language:
        scene_note += f"### Source language: {source_language}\n"

    if speaker_profile:
        name      = speaker_profile.get("name", seg.get("speaker", "Speaker"))
        archetype = speaker_profile.get("character_archetype", "").strip()
        style_dir = speaker_profile.get("style_direction", "").strip()
        profile_lines = [f"# AUDIO PROFILE: {name}"]
        if archetype:
            profile_lines.append(f"## Role: {archetype}")
        if style_dir:
            profile_lines.append(f"### Style: {style_dir}")
        profile = "\n".join(profile_lines)
        preamble = (
            f"Read the line below as the character described.\n\n"
            f"{profile}\n\n"
            f"### DIRECTOR'S NOTES\n"
            f"{scene_note}"
            f"Stay in character throughout. Honour every audio tag exactly.\n"
            f"{pacing_note}"
        )
        return f"{preamble}\n\n#### TRANSCRIPT\n{pace_tag}{tagged}"

    if scene_note:
        return f"### DIRECTOR'S NOTES\n{scene_note}{pacing_note}\n\n{pace_tag}{tagged}"
    return f"{pace_tag}{tagged}"


def _is_speaker1_name(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    return normalized in {"speaker1", "s1", "spk1"}


def build_batched_speaker_tts_input(
    segments: list[dict],
    speaker_profile: dict | None,
    pace_label: str,
    is_dubbing: bool,
    genre: str = "",
    scene_description: str = "",
    source_language: str = "",
) -> str:
    """Build one TTS request for many segments of the same speaker.

    We insert deterministic long-pause markers between lines so the returned
    audio can be split back into per-segment chunks.
    """
    if not segments:
        return ""

    lines: list[str] = []
    ordered = sorted(segments, key=lambda s: float(s.get("start", 0.0) or 0.0))
    for i, seg in enumerate(ordered):
        start_s = float(seg.get("start", 0.0) or 0.0)
        end_s = float(seg.get("end", 0.0) or 0.0)
        tagged = (seg.get("tagged_text") or seg.get("text") or "").strip()
        if not tagged:
            continue
        lines.append(f"[{start_s:.2f}-{end_s:.2f}] {tagged}")
        if i < len(ordered) - 1:
            lines.append(f"[very long pause {BATCH_SPLIT_PAUSE_MS}ms silence]")

    seg = {"tagged_text": "\n".join(lines), "speaker": ordered[0].get("speaker", "Speaker 1")}
    base = build_segment_tts_input(
        seg,
        speaker_profile=speaker_profile,
        pace_label=pace_label,
        is_dubbing=is_dubbing,
        genre=genre,
        scene_description=scene_description,
        source_language=source_language,
    )
    return (
        f"{base}\n\n"
        "### STRICT TIMELINE RULES\n"
        "- Speak only transcript lines.\n"
        f"- For every [very long pause {BATCH_SPLIT_PAUSE_MS}ms silence] marker, "
        "render true silence only (no breath/noise/voice).\n"
        f"- Keep each pause marker close to {BATCH_SPLIT_PAUSE_MS}ms.\n"
        "- Do not speak timestamps or pause markers."
    )


def split_batched_speaker_audio(
    batch_wav: Path,
    expected_count: int,
    target_durations_s: list[float] | None = None,
) -> list[AudioSegment] | None:
    """Split one speaker-batch WAV into expected segment chunks.

    Primary strategy: detect long silences between lines.
    Fallback strategy: duration-guided slicing using original segment lengths.
    """
    if expected_count <= 0:
        return []
    clip = AudioSegment.from_wav(batch_wav)
    if expected_count == 1:
        return [clip]

    # Try a few detector settings and accept the first exact match.
    for min_silence in (700, 850, 1000):
        silence_thresh = max(-55, int(round(clip.dBFS - 22))) if clip.dBFS != float("-inf") else -50
        regions = detect_nonsilent(
            clip,
            min_silence_len=min_silence,
            silence_thresh=silence_thresh,
            seek_step=10,
        )
        if len(regions) == expected_count:
            chunks: list[AudioSegment] = []
            for start_ms, end_ms in regions:
                chunks.append(clip[max(0, start_ms):min(len(clip), end_ms)])
            return chunks

    # Fallback: if silence boundaries are unreliable, split by expected segment
    # duration proportions so we can still keep one-request-per-speaker flow.
    if target_durations_s and len(target_durations_s) == expected_count:
        target_ms = [max(1, int(round(d * 1000))) for d in target_durations_s]
        total_target = sum(target_ms)
        total_clip = len(clip)
        if total_target > 0 and total_clip > 0:
            scale = total_clip / total_target
            scaled = [max(1, int(round(ms * scale))) for ms in target_ms]
            # Ensure exact total by adjusting the last chunk.
            diff = total_clip - sum(scaled)
            scaled[-1] += diff
            chunks: list[AudioSegment] = []
            cursor = 0
            for i, chunk_ms in enumerate(scaled):
                if i == len(scaled) - 1:
                    end = total_clip
                else:
                    end = max(cursor + 1, min(total_clip, cursor + chunk_ms))
                chunks.append(clip[cursor:end])
                cursor = end
            return chunks

    return None


def translate_segments_in_place(client: genai.Client, analysis: dict,
                                target_lang: str, model: str) -> bool:
    """Translate each segment's tagged_text in-place. Returns True if it ran."""
    src_lang = analysis.get("language", "en-US")
    if not target_lang or target_lang == src_lang:
        return False
    segments = analysis.get("segments", [])
    if not segments:
        return False
    print(f"[translate] per-segment -> {target_lang} ({len(segments)} segments) ...")
    for i, seg in enumerate(segments):
        original = seg.get("tagged_text") or seg.get("text", "")
        if not original.strip():
            continue
        duration_s = float(seg.get("end", 0.0) or 0.0) - float(seg.get("start", 0.0) or 0.0)
        translated = translate_tagged_transcript(
            client, original, target_lang, model,
            is_multi=False,
            target_duration_s=duration_s if duration_s > 0 else None,
        )
        seg["original_tagged_text"] = original
        seg["tagged_text"] = translated
        snippet = translated[:60] + ("..." if len(translated) > 60 else "")
        print(f"  [{i+1}/{len(segments)}] {seg.get('speaker', '?')}: {snippet}")
    return True


# ── step 5 : TTS ─────────────────────────────────────────────────────────────

def _extract_pcm_audio_bytes(response, label: str) -> bytes:
    """Extract PCM audio bytes from Gemini response with defensive checks."""
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline is not None else None
            if data:
                return data
    raise RuntimeError(
        f"{label}: model returned no audio data (empty candidates/parts). "
        "This is usually transient; retrying may succeed."
    )


def synthesise_tts_single(client: genai.Client, tts_input: str,
                          voice: str) -> bytes:
    """Single-speaker TTS."""
    print(f"[tts] single-speaker  voice='{voice}' ...")

    def _call():
        return client.models.generate_content(
            model=TTS_MODEL,
            contents=tts_input,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice)
                    )
                ),
            ),
        )

    last_exc = None
    for attempt in range(1, 4):
        try:
            response = _call_with_retry(_call, "synthesise_tts")
            pcm = _extract_pcm_audio_bytes(response, "synthesise_tts")
            return pcm_bytes_to_wav(pcm, sample_rate=24000)
        except RuntimeError as exc:
            last_exc = exc
            if attempt < 3:
                wait_s = float(attempt * 2)
                print(f"[tts] WARN: empty audio response (attempt {attempt}/3), retrying in {wait_s:.0f}s ...")
                time.sleep(wait_s)
    raise last_exc if last_exc else RuntimeError("synthesise_tts failed unexpectedly")


def synthesise_tts_multi(client: genai.Client, tts_input: str,
                         speaker_voice_map: list[dict]) -> bytes:
    """Multi-speaker TTS (up to 2 speakers)."""
    voice_configs = []
    for sv in speaker_voice_map:
        print(f"[tts] speaker='{sv['name']}'  voice='{sv['voice']}' "
              f"({VOICE_GENDER.get(sv['voice'], '?')})")
        voice_configs.append(
            types.SpeakerVoiceConfig(
                speaker=sv["name"],
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=sv["voice"])
                )
            )
        )

    def _call():
        return client.models.generate_content(
            model=TTS_MODEL,
            contents=tts_input,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                        speaker_voice_configs=voice_configs
                    )
                ),
            ),
        )

    last_exc = None
    for attempt in range(1, 4):
        try:
            response = _call_with_retry(_call, "synthesise_tts_multi")
            pcm = _extract_pcm_audio_bytes(response, "synthesise_tts_multi")
            return pcm_bytes_to_wav(pcm, sample_rate=24000)
        except RuntimeError as exc:
            last_exc = exc
            if attempt < 3:
                wait_s = float(attempt * 2)
                print(f"[tts] WARN: empty multi-speaker audio (attempt {attempt}/3), retrying in {wait_s:.0f}s ...")
                time.sleep(wait_s)
    raise last_exc if last_exc else RuntimeError("synthesise_tts_multi failed unexpectedly")


# ── batched-per-speaker parallel pipeline ─────────────────────────────────────

def _synthesize_one_speaker_batch(
    client: genai.Client,
    speaker_name: str,
    batch_items: list[tuple[int, dict]],
    voice: str,
    speaker_profile: dict | None,
    pace_label: str,
    is_dubbing: bool,
    genre: str,
    scene_description: str,
    source_language: str,
    seg_dir: Path,
    apply_tighten: bool,
) -> dict[int, Path]:
    """Synthesize one speaker's batched segments and split into per-segment WAVs.

    Returns a mapping of {segment_index -> wav_path} for all successfully
    split chunks. Falls back gracefully — an empty dict means the caller
    should synthesize each segment individually.
    """
    batched_input = build_batched_speaker_tts_input(
        [seg for _, seg in batch_items],
        speaker_profile,
        pace_label,
        is_dubbing,
        genre=genre,
        scene_description=scene_description,
        source_language=source_language,
    )
    print(
        f"\n[speaker-batch] {speaker_name}: synthesizing "
        f"{len(batch_items)} segments in ONE parallel request ..."
    )
    batch_bytes = synthesise_tts_single(client, batched_input, voice)
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", speaker_name)
    batch_wav = seg_dir / f"seg_batch_{safe_name}.wav"
    batch_wav.write_bytes(batch_bytes)

    target_durations_s = [
        max(0.05, float(seg.get("end", 0.0) or 0.0) - float(seg.get("start", 0.0) or 0.0))
        for _, seg in batch_items
    ]
    split_chunks = split_batched_speaker_audio(
        batch_wav,
        len(batch_items),
        target_durations_s=target_durations_s,
    )
    if split_chunks is None:
        print(f"[speaker-batch] split failed for {speaker_name}; will fall back to per-segment.")
        return {}

    print(f"[speaker-batch] split success for {speaker_name}: {len(split_chunks)} chunks")
    result: dict[int, Path] = {}
    for (idx, _), chunk in zip(batch_items, split_chunks):
        chunk_wav = seg_dir / f"seg_{idx+1:03d}_{safe_name}_batched.wav"
        chunk.export(chunk_wav, format="wav")
        result[idx] = chunk_wav
    return result


def synthesize_segments_to_timeline(
    client: genai.Client,
    analysis: dict,
    audio_out: Path,
    output_dir: Path,
    stem: str,
    lang_tag: str,
    target_dur: float | None,
    is_dubbing: bool,
    apply_tighten: bool,
    voice_override: str | None,
    no_speed_match: bool,
    max_speed_match: float,
    no_exact_duration: bool,
) -> Path:
    """Render N-speaker audio via batched-per-speaker parallel TTS + timeline overlay.

    Architecture (matches the Stage 4 diagram):
      1. Group all segments by speaker.
      2. For each speaker with >=2 segments, fire ONE batched TTS request
         with deterministic [pause] markers between lines.
      3. All speaker-batch requests run CONCURRENTLY via ThreadPoolExecutor.
      4. Split each speaker's audio blob back into per-segment clips via
         silence detection / duration-guided slicing.
      5. Single-segment speakers (or fallback failures) are synthesized
         per-segment — also concurrently.
      6. Overlay every finalized clip onto a single silent timeline at its
         original start timestamp.
      7. Write the result to `audio_out` and return it.
    """
    segments = analysis.get("segments", [])
    if not segments:
        raise RuntimeError(
            "synthesize_segments_to_timeline requires analysis['segments'] "
            "(per-segment timestamps). Re-run analysis without --skip-analysis."
        )

    speakers = analysis.get("speakers", [])
    speaker_by_name = {s.get("name"): s for s in speakers if s.get("name")}
    voices_by_speaker = voice_map_from_analysis(analysis)
    pace_label = analysis.get("pace", "moderate")
    genre = analysis.get("genre", "")
    scene_description = analysis.get("scene_description", "")
    source_language = analysis.get("language", "")

    seg_dir = output_dir / f"{stem}_segments{lang_tag}"
    seg_dir.mkdir(exist_ok=True)

    if target_dur and target_dur > 0:
        total_ms = int(round(target_dur * 1000))
    else:
        last_end = max(float(s.get("end", 0.0) or 0.0) for s in segments)
        total_ms = int(round(last_end * 1000)) + 500
    print(f"[timeline] total length = {total_ms / 1000.0:.2f}s  ({len(segments)} segments)")

    def _resolve_voice(speaker_name: str) -> str:
        if voice_override:
            return voice_override
        return voices_by_speaker.get(
            speaker_name,
            "Charon" if (speaker_by_name.get(speaker_name, {}).get("gender") == "male") else "Aoede",
        )

    # ── Phase 1: group segments by speaker, build batch eligibility ──────────
    segments_by_speaker: dict[str, list[tuple[int, dict]]] = {}
    for idx, seg in enumerate(segments):
        spk = str(seg.get("speaker", "Speaker?") or "Speaker?")
        segments_by_speaker.setdefault(spk, []).append((idx, seg))

    # Eligible batch items per speaker (seg_dur >= 0.4 and non-empty text).
    batchable: dict[str, list[tuple[int, dict]]] = {}
    for speaker_name, indexed_segs in segments_by_speaker.items():
        items: list[tuple[int, dict]] = []
        for idx, seg in indexed_segs:
            start_s = float(seg.get("start", 0.0) or 0.0)
            end_s   = float(seg.get("end", 0.0) or 0.0)
            seg_dur = max(0.0, end_s - start_s)
            text = (seg.get("tagged_text") or seg.get("text", "")).strip()
            if seg_dur < 0.4 or not text:
                continue
            seg_prepared = {**seg, "tagged_text": dub_tighten_tags(text)} if apply_tighten else seg
            items.append((idx, seg_prepared))
        if len(items) >= 2:
            batchable[speaker_name] = items

    # ── Phase 2: fire all speaker-batch TTS calls concurrently ───────────────
    batched_wavs_by_index: dict[int, Path] = {}
    batch_max_workers = max(1, len(batchable))

    if batchable:
        print(f"\n[speaker-batch] launching {len(batchable)} parallel speaker requests ...")
        with ThreadPoolExecutor(max_workers=batch_max_workers) as executor:
            futures = {
                executor.submit(
                    _synthesize_one_speaker_batch,
                    client,
                    speaker_name,
                    items,
                    _resolve_voice(speaker_name),
                    speaker_by_name.get(speaker_name),
                    pace_label,
                    is_dubbing,
                    genre,
                    scene_description,
                    source_language,
                    seg_dir,
                    apply_tighten,
                ): speaker_name
                for speaker_name, items in batchable.items()
            }
            for future in as_completed(futures):
                spk = futures[future]
                try:
                    result = future.result()
                    batched_wavs_by_index.update(result)
                    print(f"[speaker-batch] {spk}: {len(result)} chunks ready.")
                except Exception as exc:
                    print(f"[speaker-batch] {spk}: FAILED ({exc}). Will fall back per-segment.")

    # ── Phase 3: finalize every segment (speed-match, exact-dur, collect) ────
    seg_wavs: list[tuple[dict, Path]] = []
    last_end_ms = -1

    for i, seg in enumerate(segments):
        speaker_name = seg.get("speaker", "Speaker?")
        start_s = float(seg.get("start", 0.0) or 0.0)
        end_s   = float(seg.get("end", 0.0) or 0.0)
        seg_dur = max(0.0, end_s - start_s)
        start_ms = int(round(start_s * 1000))

        if start_ms < last_end_ms:
            print(f"[timeline] WARN: segment {i+1} ({speaker_name}) starts at "
                  f"{start_s:.2f}s but previous ended at {last_end_ms/1000:.2f}s "
                  f"— they will overlap (mixed).")
        last_end_ms = int(round(end_s * 1000))

        if seg_dur < 0.4:
            print(f"[timeline] skip seg {i+1} ({speaker_name}) — dur {seg_dur:.2f}s < 0.4s")
            continue

        text = seg.get("tagged_text") or seg.get("text", "")
        if not text.strip():
            print(f"[timeline] skip seg {i+1} ({speaker_name}) — empty text")
            continue

        voice = _resolve_voice(speaker_name)
        speaker_profile = speaker_by_name.get(speaker_name)

        if i in batched_wavs_by_index:
            seg_wav = batched_wavs_by_index[i]
            print(f"\n[seg {i+1}/{len(segments)}] {speaker_name} "
                  f"@ {start_s:.2f}-{end_s:.2f}s  source='speaker-batch'")
            print(f"  text: {text[:80]}{'...' if len(text) > 80 else ''}")

            # Retry batched-underfilled segments directly
            try:
                raw_dur = get_audio_duration(seg_wav)
                raw_ratio = (raw_dur / seg_dur) if seg_dur > 0 else 1.0
            except Exception:
                raw_dur, raw_ratio = 0.0, 1.0

            if is_dubbing and seg_dur > 0 and raw_ratio < 0.78:
                print(f"  [batch-underfill] clip {raw_dur:.2f}s for {seg_dur:.2f}s "
                      f"(ratio={raw_ratio:.2f}). Regenerating directly ...")
                seg_for_tts_retry = {**seg}
                if apply_tighten:
                    seg_for_tts_retry["tagged_text"] = dub_tighten_tags(
                        seg.get("tagged_text") or seg.get("text", "")
                    )
                retry_input = build_segment_tts_input(
                    seg_for_tts_retry, speaker_profile, pace_label, is_dubbing,
                    genre=genre, scene_description=scene_description,
                    source_language=source_language,
                )
                safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", speaker_name)
                retry_bytes = synthesise_tts_single(client, retry_input, voice)
                seg_wav = seg_dir / f"seg_{i+1:03d}_{safe_name}_regen.wav"
                seg_wav.write_bytes(retry_bytes)
                try:
                    raw_dur = get_audio_duration(seg_wav)
                    raw_ratio = (raw_dur / seg_dur) if seg_dur > 0 else 1.0
                    print(f"  [batch-underfill] regen: {raw_dur:.2f}s (ratio={raw_ratio:.2f})")
                except Exception:
                    pass

        else:
            # Per-segment fallback (single-segment speakers or batch failures)
            seg_for_tts = {**seg}
            if apply_tighten:
                tightened = dub_tighten_tags(seg.get("tagged_text") or seg.get("text", ""))
                seg_for_tts["tagged_text"] = tightened

            tts_input = build_segment_tts_input(
                seg_for_tts, speaker_profile, pace_label, is_dubbing,
                genre=genre, scene_description=scene_description,
                source_language=source_language,
            )
            print(f"\n[seg {i+1}/{len(segments)}] {speaker_name} "
                  f"@ {start_s:.2f}-{end_s:.2f}s  voice='{voice}' "
                  f"({VOICE_GENDER.get(voice, '?')})")
            print(f"  text: {text[:80]}{'...' if len(text) > 80 else ''}")

            safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", speaker_name)
            wav_bytes = synthesise_tts_single(client, tts_input, voice)
            seg_wav = seg_dir / f"seg_{i+1:03d}_{safe_name}.wav"
            seg_wav.write_bytes(wav_bytes)

            # Underfill retry — avoid robotic 0.6x rubberband stretching
            try:
                raw_dur = get_audio_duration(seg_wav)
                raw_ratio = (raw_dur / seg_dur) if seg_dur > 0 else 1.0
            except Exception:
                raw_dur, raw_ratio = 0.0, 1.0

            if is_dubbing and seg_dur > 0 and raw_ratio < 0.55:
                print(f"  [underfill] raw {raw_dur:.2f}s for {seg_dur:.2f}s "
                      f"(ratio={raw_ratio:.2f}). Retrying with relaxed pacing ...")
                retry_seg = {**seg, "tagged_text": seg.get("text", text)}
                retry_input = build_segment_tts_input(
                    retry_seg, speaker_profile,
                    pace_label="moderate-slow", is_dubbing=False,
                    genre=genre, scene_description=scene_description,
                    source_language=source_language,
                )
                retry_bytes = synthesise_tts_single(client, retry_input, voice)
                seg_wav.write_bytes(retry_bytes)
                try:
                    retry_dur = get_audio_duration(seg_wav)
                    print(f"  [underfill] retry: {retry_dur:.2f}s "
                          f"(was {raw_dur:.2f}s, target {seg_dur:.2f}s)")
                except Exception:
                    pass

        # Speed-match and exact-duration (applied regardless of batch vs direct)
        if not no_speed_match and seg_dur > 0:
            try:
                cap = DUBBING_MAX_TEMPO if is_dubbing else max_speed_match
                floor_speed = DUBBING_MIN_TEMPO if is_dubbing else 0.85
                speed_match_wav(seg_wav, seg_dur,
                                max_speed=cap, min_speed=floor_speed,
                                segment_dur_s=seg_dur)
            except Exception as exc:
                print(f"  [speed-match] skipped: {exc}")

        if not no_exact_duration and seg_dur > 0:
            try:
                force_exact_duration(seg_wav, seg_dur)
            except Exception as exc:
                print(f"  [exact-dur] skipped: {exc}")

        seg_wavs.append((seg, seg_wav))

    if not seg_wavs:
        raise RuntimeError(
            "No segments were synthesized — every segment was skipped. "
            "Check the analysis output."
        )

    # ── Phase 4: mix all clips onto a silent timeline ─────────────────────────
    print(f"\n[timeline] overlaying {len(seg_wavs)} clips onto "
          f"{total_ms / 1000:.2f}s silence ...")
    mixed = AudioSegment.silent(duration=total_ms)
    for seg, seg_wav in seg_wavs:
        clip = AudioSegment.from_wav(seg_wav)
        position = int(round(float(seg.get("start", 0.0) or 0.0) * 1000))
        mixed = mixed.overlay(clip, position=position)

    # Write to the caller-specified audio_out path (main.py expects final.wav there)
    audio_out.parent.mkdir(parents=True, exist_ok=True)
    mixed.export(str(audio_out), format="wav")
    print(f"[saved] timeline mix -> {audio_out}")
    return audio_out


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Experiment: tone-matched TTS with multi-speaker support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input",
        help="Path to input audio or video file (10-60s recommended)")
    parser.add_argument("--output", "-o", default=None,
        help="Output WAV path (default: <stem>_tts.wav)")
    parser.add_argument("--voice", "-v", default=None,
        help="Override voice for single-speaker (ignored in multi-speaker)")
    parser.add_argument("--model", "-m", default="gemini-2.5-pro",
        help="Model for step 3 analysis (default: gemini-2.5-pro)")
    parser.add_argument("--target-lang", "-t", default=None,
        help="Target language for dubbing, e.g. hi-IN (default: same as source)")
    parser.add_argument("--cached-analysis", default=None, metavar="JSON",
        help="Path to saved analysis JSON from a previous run")
    parser.add_argument("--skip-analysis", action="store_true",
        help="Skip step 3 analysis (requires --cached-analysis)")
    parser.add_argument("--max-duration", type=float, default=60.0,
        help="Max seconds of audio to extract (default: 60)")
    parser.add_argument("--analysis-only", action="store_true",
        help="Only run step 3 (analysis); skip TTS")
    parser.add_argument("--no-speed-match", action="store_true",
        help="Skip auto-matching TTS duration to source (via ffmpeg rubberband)")
    parser.add_argument("--max-speed-match", type=float, default=1.2,
        help="Max rubberband tempo for duration matching (default: 1.2). "
             "Higher values match tighter but can sound rushed.")
    parser.add_argument("--no-dub-tighten", action="store_true",
        help="When dubbing, don't auto-strip [slow] and downgrade pause tags")
    parser.add_argument("--no-exact-duration", action="store_true",
        help="Skip final pad/trim that snaps the WAV to source duration exactly")
    parser.add_argument("--mux", action="store_true",
        help="Also produce an .mp4 with the TTS audio replacing the "
             "original video's audio (only when input is a video)")
    parser.add_argument("--no-mux", action="store_true",
        help="Skip muxing even if input is video")
    parser.add_argument("--multi-speaker-mode",
        choices=("auto", "single-per-segment", "gemini-multi"),
        default="auto",
        help="How to render multi-speaker clips. "
             "'gemini-multi' uses Gemini's MultiSpeakerVoiceConfig (max 2 speakers). "
             "'single-per-segment' synthesizes each speaker's segments separately "
             "with single-speaker TTS, then stitches them on the original timeline "
             "(supports any number of speakers). "
             "'auto' (default) picks gemini-multi for <=2 speakers, "
             "single-per-segment for 3+.")
    args = parser.parse_args()

    if not GEMINI_API_KEY:
        sys.exit("ERROR: GEMINI_API_KEY not set. Add it to backend/.env")

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        sys.exit(f"ERROR: file not found: {input_path}")

    stem    = input_path.stem
    base_dir = Path(__file__).resolve().parent.parent  # backend/
    output_dir   = base_dir / "output"
    analysis_dir = base_dir / "analysis"
    analysis_hindi_dir = analysis_dir / "hindi"
    analysis_english_dir = analysis_dir / "english"
    output_dir.mkdir(exist_ok=True)
    analysis_dir.mkdir(exist_ok=True)
    analysis_hindi_dir.mkdir(exist_ok=True)
    analysis_english_dir.mkdir(exist_ok=True)
    reorganize_existing_analysis_files(analysis_dir)

    lang_tag = f"_{args.target_lang.replace('-', '')}" if args.target_lang else ""
    audio_out = (Path(args.output) if args.output
                 else output_dir / f"{stem}_tts{lang_tag}.wav")

    # ── extract audio if needed ──
    if input_path.suffix.lower() == ".wav":
        work_audio = input_path
        _cleanup = False
    else:
        if not _has_ffmpeg():
            sys.exit("ERROR: ffmpeg not found on PATH.")
        if args.multi_speaker_mode == "single-per-segment":
            extract_dir = output_dir / f"{stem}_segments{lang_tag}"
            extract_dir.mkdir(exist_ok=True)
        else:
            extract_dir = output_dir
        work_audio = extract_dir / f"{stem}_extracted.wav"
        _cleanup = True
        extract_audio_to_wav(input_path, work_audio, args.max_duration)

    client = genai.Client(api_key=GEMINI_API_KEY)

    # ── step 3 : analyse (or load cache) ──
    if args.skip_analysis or args.cached_analysis:
        if not args.cached_analysis:
            sys.exit("ERROR: --skip-analysis requires --cached-analysis <file.json>")
        cached = Path(args.cached_analysis).resolve()
        if not cached.exists():
            sys.exit(f"ERROR: cached analysis not found: {cached}")
        analysis = json.loads(cached.read_text(encoding="utf-8"))
        print(f"[analyse] loaded from cache: {cached}")
    else:
        analysis = analyse_audio(client, work_audio, args.model)

    num_speakers = analysis.get("num_speakers", 1)
    speakers = analysis.get("speakers", [])
    segments = analysis.get("segments", [])
    is_multi = num_speakers >= 2 and len(speakers) >= 2

    # Resolve the effective multi-speaker rendering mode.
    if args.multi_speaker_mode == "auto":
        if num_speakers >= 3 and segments:
            effective_mode = "single-per-segment"
        elif num_speakers >= 3 and not segments:
            print("[mode] num_speakers>=3 but analysis has no 'segments' "
                  "(probably an old cached analysis). Falling back to gemini-multi.")
            effective_mode = "gemini-multi"
        else:
            effective_mode = "gemini-multi"
    else:
        effective_mode = args.multi_speaker_mode
        if effective_mode == "single-per-segment" and not segments:
            sys.exit("ERROR: --multi-speaker-mode=single-per-segment requires "
                     "'segments' in the analysis JSON. Re-run analysis (don't "
                     "use --skip-analysis with an old cached file).")

    use_segments = (effective_mode == "single-per-segment")
    print(f"[mode] multi-speaker rendering = {effective_mode} "
          f"(num_speakers={num_speakers}, segments={len(segments)})")

    sep = "-" * 60
    print(f"\n{sep}")
    print(f"STEP 3 RESULT  (model: {args.model})")
    print(sep)
    print(f"  Genre      : {analysis.get('genre', '?')}")
    print(f"  Scene      : {analysis.get('scene_description', '?')}")
    print(f"  Register   : {analysis.get('register', '?')}")
    print(f"  Language   : {analysis.get('language', '?')}")
    print(f"  Speakers   : {num_speakers}")
    for i, s in enumerate(speakers):
        print(f"    [{s.get('name', f'S{i+1}')}] {s.get('gender', '?')}  "
              f"archetype=\"{s.get('character_archetype', '?')}\"")
        print(f"      voice = {s.get('recommended_voice', '?')}  "
              f"(reason: {s.get('voice_reasoning', '')})")
        print(f"      style = {s.get('style_direction', '')}")
    print(f"  Pace       : {analysis.get('pace', '?')}")
    print(f"  Segments   : {len(segments)}")
    print(f"\n  Transcript (clean):")
    print(f"    {analysis.get('transcript', '')[:300]}...")
    print(f"\n  Tagged Transcript:")
    print(f"    {analysis.get('tagged_transcript', '')[:400]}...")
    if segments:
        print(f"\n  Speaker Plan (VROTT separates same-speaker segments):")
        plan_text = render_speaker_plan_text(build_speaker_plan(analysis))
        for line in plan_text.splitlines():
            print(f"    {line}")
    print(sep)

    # build the output path now so --analysis-only and the final save both use it
    analysis_filename = f"{stem}_analysis{lang_tag}.json"
    analysis_lang     = args.target_lang or analysis.get("language")
    analysis_bucket   = analysis_language_bucket(analysis_lang)
    analysis_out      = analysis_dir / analysis_bucket / analysis_filename

    if args.analysis_only:
        analysis_out_data = dict(analysis)
        if segments:
            analysis_out_data["speaker_plan"] = build_speaker_plan(analysis)
        analysis_out.write_text(
            json.dumps(analysis_out_data, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"\n[saved] analysis -> {analysis_out}")
        print("\nAnalysis-only mode. Done.")
        if _cleanup and work_audio.exists():
            work_audio.unlink()
        return

    # ── compute target duration up front (used for dub-aware choices) ──
    target_dur: float | None = None
    if input_path.suffix.lower() != ".wav":
        try:
            target_dur = get_audio_duration(input_path)
            if args.max_duration:
                target_dur = min(target_dur, args.max_duration)
        except Exception as exc:
            print(f"[duration] could not probe source: {exc}")

    # ── step 4 (optional) : translate ──
    tagged = analysis.get("tagged_transcript", analysis.get("transcript", ""))
    is_dubbing = False
    if args.target_lang:
        source_lang = analysis.get("language", "en-US")
        if args.target_lang != source_lang:
            is_dubbing = True
            if use_segments:
                # Per-segment translation keeps timing alignment intact —
                # mutates analysis['segments'][i]['tagged_text'] in place.
                translate_segments_in_place(
                    client, analysis, args.target_lang, args.model)
            else:
                tagged = translate_tagged_transcript(
                    client, tagged, args.target_lang, args.model,
                    is_multi=is_multi, target_duration_s=target_dur)
                print(f"\n  Translated tagged transcript:")
                print(f"    {tagged[:400]}{'...' if len(tagged) > 400 else ''}")

    # ── dub-aware tightening ──
    apply_tighten = is_dubbing and not args.no_dub_tighten
    if apply_tighten and not use_segments:
        # For per-segment mode, tightening is applied right before each
        # segment's TTS call inside synthesize_segments_to_timeline().
        tagged = dub_tighten_tags(tagged)

    # ── save analysis (after translation so the JSON includes translated text) ──
    save_data = dict(analysis)
    if is_dubbing:
        if not use_segments:
            save_data["original_tagged_transcript"] = analysis.get("tagged_transcript", "")
            save_data["tagged_transcript"] = tagged
        save_data["dubbed_language"] = args.target_lang
    if analysis.get("segments"):
        save_data["speaker_plan"] = build_speaker_plan(analysis)
    save_data["multi_speaker_mode_used"] = effective_mode
    analysis_out.write_text(
        json.dumps(save_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[saved] analysis -> {analysis_out}")

    # ── step 5 : TTS ──
    if use_segments:
        audio_out = synthesize_segments_to_timeline(
            client=client,
            analysis=analysis,
            audio_out=audio_out,
            output_dir=output_dir,
            stem=stem,
            lang_tag=lang_tag,
            target_dur=target_dur,
            is_dubbing=is_dubbing,
            apply_tighten=apply_tighten,
            voice_override=args.voice,
            no_speed_match=args.no_speed_match,
            max_speed_match=args.max_speed_match,
            no_exact_duration=args.no_exact_duration,
        )
    else:
        # ── build TTS input (legacy <=2 speaker path) ──
        pace = analysis.get("pace", "moderate")
        genre = analysis.get("genre", "")
        scene_description = analysis.get("scene_description", "")
        pace_tag = ""
        if apply_tighten:
            # Don't force [fast] blindly; respect the character's original pace
            # but just ensure it doesn't drag too much.
            if pace in ("slow", "very slow", "moderate-slow"):
                pace_tag = ""
            else:
                pace_tag = "[fast] " if pace in ("fast", "very fast", "moderate-fast") else ""
        elif pace in ("fast", "very fast", "moderate-fast"):
            pace_tag = "[fast] "
        elif pace in ("slow", "very slow", "moderate-slow"):
            pace_tag = "[slow] "

        pace_label = analysis.get("pace", "moderate")
        source_language = analysis.get("language", "")

        # Build a scene context line for the TTS director note
        scene_note = ""
        if genre:
            scene_note = f"### Scene type: {genre}"
            if scene_description:
                scene_note += f" — {scene_description}"
            scene_note += "\n"
        if source_language:
            scene_note += f"### Source language: {source_language}\n"

        def _profile_block(s: dict) -> str:
            name      = s.get("name", "Speaker")
            archetype = s.get("character_archetype", "").strip()
            style_dir = s.get("style_direction", "").strip()
            lines = [f"# AUDIO PROFILE: {name}"]
            if archetype:
                lines.append(f"## Role: {archetype}")
            if style_dir:
                lines.append(f"### Style: {style_dir}")
            return "\n".join(lines)

        if is_dubbing:
            pacing_note = (
                "### Pacing: natural conversational speed.\n"
                "* DO NOT add any pauses beyond what's explicitly tagged.\n"
                "* DO NOT linger on words or add dramatic breaths.\n"
                "* Keep gaps between sentences to a natural minimum.\n"
                "* First understand the scene and dialogue-delivery timing before speaking.\n"
                "* Keep the target dubbed language clear, but let delivery carry subtle source-language accent influence from the original audio.\n"
                "* If the source language is naturally fast (for example Spanish), keep energetic turn-taking and cadence while staying intelligible in the dubbed language.\n"
                "* After translation, align each character's speed to scene timing and emotional delivery.\n"
                "* If a sentence is long, increase speed as needed while keeping speech clear and natural.\n"
                "* If a sentence is very short, either add a gentle trailing '...' to extend delivery or adjust speed to fit naturally.\n"
                "* This is a dub fitting a fixed video duration - efficient delivery is key."
            )
        else:
            pacing_note = f"### Pacing: {pace_label}, natural rhythm, no rushing."

        if is_multi:
            profiles = "\n\n".join(_profile_block(s) for s in speakers)
            preamble = (
                f"TTS the following conversation between the speakers below.\n\n"
                f"{profiles}\n\n"
                f"### DIRECTOR'S NOTES\n"
                f"{scene_note}"
                f"Each speaker should stay in character throughout. Honour every\n"
                f"audio tag in the transcript exactly as written.\n"
                f"{pacing_note}"
            )
            tts_input = f"{preamble}\n\n#### TRANSCRIPT\n{pace_tag}{tagged}"
        else:
            if speakers:
                profile = _profile_block(speakers[0])
                preamble = (
                    f"Read the transcript below as the character described.\n\n"
                    f"{profile}\n\n"
                    f"### DIRECTOR'S NOTES\n"
                    f"{scene_note}"
                    f"Stay in character throughout. Honour every audio tag exactly.\n"
                    f"{pacing_note}"
                )
                tts_input = f"{preamble}\n\n#### TRANSCRIPT\n{pace_tag}{tagged}"
            else:
                tts_input = f"{pace_tag}{tagged}"

        print(f"\n[tts input]:")
        print(f"  {tts_input[:500]}{'...' if len(tts_input) > 500 else ''}\n")

        if is_multi:
            speaker_voice_map = []
            for s in speakers[:2]:
                voice = s.get("recommended_voice", "Charon")
                if voice not in VOICE_GENDER:
                    voice = "Charon" if s.get("gender") == "male" else "Aoede"
                speaker_voice_map.append({"name": s["name"], "voice": voice})
            wav_bytes = synthesise_tts_multi(client, tts_input, speaker_voice_map)
        else:
            if args.voice:
                chosen_voice = args.voice
                print(f"[voice] manual override: '{chosen_voice}'")
            elif speakers:
                chosen_voice = speakers[0].get("recommended_voice", "")
                if chosen_voice not in VOICE_GENDER:
                    g = speakers[0].get("gender", "unknown")
                    chosen_voice = "Charon" if g == "male" else "Aoede"
                print(f"[voice] auto: '{chosen_voice}' ({VOICE_GENDER.get(chosen_voice, '?')})")
            else:
                chosen_voice = "Charon"
            wav_bytes = synthesise_tts_single(client, tts_input, chosen_voice)

        audio_out.write_bytes(wav_bytes)
        print(f"[saved] TTS audio -> {audio_out}")

        # ── auto speed-match to original duration (legacy path only — for the
        # per-segment path each clip is already speed-matched individually) ──
        if not args.no_speed_match and target_dur is not None:
            try:
                if is_dubbing:
                    auto_speed_cap = DUBBING_MAX_TEMPO
                    auto_min_speed = DUBBING_MIN_TEMPO
                else:
                    auto_speed_cap = args.max_speed_match
                    auto_min_speed = 0.85
                speed_match_wav(audio_out, target_dur,
                                max_speed=auto_speed_cap, min_speed=auto_min_speed)
            except Exception as exc:
                print(f"[speed-match] skipped due to error: {exc}")

        if not args.no_exact_duration and target_dur is not None:
            try:
                force_exact_duration(audio_out, target_dur)
            except Exception as exc:
                print(f"[exact-dur] skipped due to error: {exc}")

    # ── optional mux: combine original video + new TTS audio into .mp4 ──
    muxed_out: Path | None = None
    is_video_input = input_path.suffix.lower() in VIDEO_EXTENSIONS
    should_mux = is_video_input and not args.no_mux and (args.mux or True)
    if should_mux and _has_ffmpeg():
        if use_segments:
            muxed_out = output_dir / f"{stem}_segments{lang_tag}" / f"{stem}_tts{lang_tag}.mp4"
        else:
            muxed_out = output_dir / f"{stem}_tts{lang_tag}.mp4"
        try:
            mux_audio_into_video(input_path, audio_out, muxed_out)
            print(f"[saved] muxed video -> {muxed_out}")
        except subprocess.CalledProcessError as exc:
            print(f"[mux] failed: {exc}")
            muxed_out = None

    # ── cleanup ──
    if _cleanup and work_audio.exists():
        work_audio.unlink()

    print(f"\n=== Done ===")
    print(f"  Input        : {input_path}")
    print(f"  Analysis     : {analysis_out}")
    print(f"  TTS output   : {audio_out}")
    if muxed_out:
        print(f"  Muxed video  : {muxed_out}")
    print(f"  Speakers     : {num_speakers}")
    print(f"  Render mode  : {effective_mode}")
    for s in speakers:
        print(f"    {s.get('name', '?')} -> {s.get('recommended_voice', '?')} "
              f"({s.get('gender', '?')})")
    print(f"  Step 3 model : {args.model}")
    print(f"  TTS model    : {TTS_MODEL}")
    if args.target_lang:
        print(f"  Dubbed       : {analysis.get('language', '?')} -> {args.target_lang}")


if __name__ == "__main__":
    main()