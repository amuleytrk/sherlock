import { useEffect, useRef, useState } from "react";

/**
 * A playful "I'm working on it" indicator inspired by Claude Code's rotating
 * gerunds (Razzmatazing, Pondering, etc.). Mixes Sherlock-detective vibes
 * with Trackonomy-domain in-jokes so it feels like Sherlock specifically.
 *
 * Renders as:    ●  Razzmatazing…
 * where the dot pulses softly and the word cycles every ~2.5s with a tiny
 * fade-in transition.
 */
const WORDS = [
  // Detective-flavored
  "Sleuthing",
  "Investigating",
  "Deducing",
  "Examining clues",
  "Cogitating",
  "Pondering",
  "Cross-referencing",
  "Following threads",
  "Magnifying",
  "Snooping around",
  "Sniffing the trail",
  "Combing evidence",
  "Drawing inferences",
  "Untangling",
  "Hypothesizing",
  "Sussing it out",
  "Detecting",
  "Probing",
  "Hunting clues",
  "Connecting dots",

  // Cognitive / agentic
  "Synthesizing",
  "Triangulating",
  "Crystallizing",
  "Distilling",
  "Marinating",
  "Mulling",
  "Wrangling",
  "Tinkering",
  "Brainstorming",
  "Fossicking",

  // Trackonomy-domain in-jokes
  "Tape-tracing",
  "Lime-spotting",
  "Decoding G1 packets",
  "Hex-deciphering",
  "Correlating across services",
  "Walking the bug-tree",

  // BBC Sherlock (Benedict Cumberbatch) homages
  "Mind palacing",          // "I need to go to my mind palace"
  "Noticing, not knowing",  // "I didn't know, I noticed"
  "Cumberbatching",         // for the man himself
  "Holmes-ing it",
  "Game afooting",          // "the game is afoot"
  "Out-witting Moriarty",
  "Reichenbach-ing",        // for the fans who lost a year to that cliffhanger

  // The Claude Code homage
  "Razzmatazing",
  "Discombobulating",
  "Reticulating splines",
];

function pickWord(prev) {
  // Pick a fresh word that isn't the same as the previous one.
  if (WORDS.length < 2) return WORDS[0];
  let next = prev;
  while (next === prev) {
    next = WORDS[Math.floor(Math.random() * WORDS.length)];
  }
  return next;
}

export default function ThinkingIndicator({ intervalMs = 2500 }) {
  const [word, setWord] = useState(() => pickWord(null));
  const wordRef = useRef(word);
  wordRef.current = word;

  useEffect(() => {
    const id = setInterval(() => {
      setWord(pickWord(wordRef.current));
    }, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);

  return (
    <div className="flex items-center gap-2 py-1 text-sm select-none">
      <span
        className="inline-block w-2 h-2 rounded-full bg-primary animate-pulse-soft"
        aria-hidden
      />
      <span className="font-tech text-primary">
        {/* `key` forces React to remount the span so the fade-in keyframe
            fires on each new word. */}
        <span key={word} className="inline-block animate-fade-in-word">
          {word}
        </span>
        <span className="text-ink-muted ml-0.5 animate-ellipsis">…</span>
      </span>
    </div>
  );
}
