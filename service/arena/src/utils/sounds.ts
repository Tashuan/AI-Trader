// Sound utility — generates short tones via Web Audio API for arena events

let audioCtx: AudioContext | null = null;
let muted = localStorage.getItem('arena_sounds_muted') === 'true';

export function isMuted() {
  return muted;
}

export function setMuted(value: boolean) {
  muted = value;
  localStorage.setItem('arena_sounds_muted', String(value));
}

function getCtx(): AudioContext | null {
  if (muted) return null;
  if (!audioCtx) {
    try {
      audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
    } catch {
      return null;
    }
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
  return audioCtx;
}

function playTone(
  freq: number,
  duration: number,
  type: OscillatorType = 'sine',
  volume: number = 0.15,
  delay: number = 0,
) {
  const ctx = getCtx();
  if (!ctx) return;

  const osc = ctx.createOscillator();
  const gain = ctx.createGain();

  osc.type = type;
  osc.frequency.value = freq;

  const start = ctx.currentTime + delay;
  gain.gain.setValueAtTime(0, start);
  gain.gain.linearRampToValueAtTime(volume, start + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.001, start + duration);

  osc.connect(gain);
  gain.connect(ctx.destination);

  osc.start(start);
  osc.stop(start + duration);
}

function playSequence(notes: { freq: number; dur: number; type?: OscillatorType; vol?: number }[]) {
  let elapsed = 0;
  for (const note of notes) {
    playTone(note.freq, note.dur, note.type ?? 'sine', note.vol ?? 0.15, elapsed);
    elapsed += note.dur * 0.7;
  }
}

// Sound presets
export const sounds = {
  // Buy — ascending two-tone
  buy: () => playSequence([
    { freq: 523, dur: 0.12, type: 'sine', vol: 0.12 },
    { freq: 784, dur: 0.15, type: 'sine', vol: 0.12 },
  ]),

  // Sell — descending two-tone
  sell: () => playSequence([
    { freq: 784, dur: 0.12, type: 'sine', vol: 0.12 },
    { freq: 523, dur: 0.15, type: 'sine', vol: 0.12 },
  ]),

  // Stop loss — harsh low buzz
  stopLoss: () => playSequence([
    { freq: 200, dur: 0.08, type: 'sawtooth', vol: 0.1 },
    { freq: 150, dur: 0.12, type: 'sawtooth', vol: 0.1 },
    { freq: 100, dur: 0.18, type: 'sawtooth', vol: 0.1 },
  ]),

  // Take profit — bright triad
  takeProfit: () => playSequence([
    { freq: 659, dur: 0.1, type: 'sine', vol: 0.12 },
    { freq: 880, dur: 0.1, type: 'sine', vol: 0.12 },
    { freq: 1047, dur: 0.18, type: 'sine', vol: 0.14 },
  ]),

  // New message / discussion — soft chime
  message: () => playSequence([
    { freq: 880, dur: 0.08, type: 'triangle', vol: 0.08 },
    { freq: 1100, dur: 0.1, type: 'triangle', vol: 0.08 },
  ]),

  // Strategy published — gentle arpeggio
  strategy: () => playSequence([
    { freq: 523, dur: 0.08, type: 'triangle', vol: 0.08 },
    { freq: 659, dur: 0.08, type: 'triangle', vol: 0.08 },
    { freq: 880, dur: 0.12, type: 'triangle', vol: 0.08 },
  ]),
};

// Map a WsActivityEvent to the appropriate sound
export function playSoundForEvent(msg: {
  message_type: string;
  action?: string;
  signal_type?: string;
  side?: string;
  content?: string;
  title?: string;
}) {
  if (muted) return;

  if (msg.message_type === 'operation') {
    const action = (msg.action || msg.signal_type || msg.side || '').toLowerCase();
    if (action.includes('stop') || action.includes('sl')) {
      sounds.stopLoss();
    } else if (action.includes('take') || action.includes('tp') || action.includes('profit')) {
      sounds.takeProfit();
    } else if (action === 'buy' || action === 'long') {
      sounds.buy();
    } else if (action === 'sell' || action === 'short' || action === 'close') {
      sounds.sell();
    } else {
      sounds.buy();
    }
  } else if (msg.message_type === 'strategy') {
    sounds.strategy();
  } else if (msg.message_type === 'discussion' || msg.message_type === 'reply') {
    sounds.message();
  }
}
