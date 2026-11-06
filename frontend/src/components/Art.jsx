// Small drawn illustrations used on the chat page (inline SVG, no image files).

export function LogoMark({ size = 30 }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true" fill="none" strokeLinecap="round" strokeLinejoin="round">
      <defs><linearGradient id="cl" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#1f9bff" /><stop offset="1" stopColor="#6a5cff" /></linearGradient></defs>
      <path d="M17 27H9a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v6" stroke="url(#cl)" strokeWidth="2.2" />
      <path d="M11 11h6M11 15h4" stroke="url(#cl)" strokeWidth="2.2" />
      <circle cx="21" cy="21" r="4.5" stroke="url(#cl)" strokeWidth="2.2" />
      <path d="m24.5 24.5 3.5 3.5" stroke="url(#cl)" strokeWidth="2.2" />
    </svg>
  );
}

// The little robot that sits next to each answer.
export function RobotAvatar() {
  return (
    <svg viewBox="0 0 64 64" width="26" height="26" aria-hidden="true">
      <defs>
        <linearGradient id="raW" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#fff" /><stop offset="1" stopColor="#c3d6ff" /></linearGradient>
        <linearGradient id="raS" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#0a1236" /><stop offset="1" stopColor="#1b2670" /></linearGradient>
      </defs>
      <rect x="3" y="26" width="6" height="14" rx="3" fill="#5b8cff" /><rect x="55" y="26" width="6" height="14" rx="3" fill="#5b8cff" />
      <rect x="8" y="12" width="48" height="42" rx="18" fill="url(#raW)" />
      <rect x="13" y="17" width="38" height="30" rx="13" fill="url(#raS)" />
      <g stroke="#7fe8ff" strokeWidth="2.8" strokeLinecap="round" fill="none"><path d="M21 31q4-6 8 0" /><path d="M35 31q4-6 8 0" /><path d="M27 38q5 4 10 0" /></g>
    </svg>
  );
}

// Empty-state illustration: robot, question bubble and a PDF.
export function EmptyArt() {
  return (
    <svg className="empty-art" viewBox="0 0 440 400" role="img" aria-label="A friendly robot with a PDF, thinking of a question">
      <defs>
        <radialGradient id="eGlow" cx=".5" cy=".5" r=".5"><stop offset="0" stopColor="#4fb3ff" stopOpacity=".55" /><stop offset="1" stopColor="#4fb3ff" stopOpacity="0" /></radialGradient>
        <linearGradient id="eBubble" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#7cc8ff" /><stop offset="1" stopColor="#6a7bff" /></linearGradient>
        <linearGradient id="eWhite" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#ffffff" /><stop offset="1" stopColor="#c3d6ff" /></linearGradient>
        <linearGradient id="eScreen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#0a1236" /><stop offset="1" stopColor="#1b2670" /></linearGradient>
      </defs>
      <circle cx="235" cy="225" r="175" fill="url(#eGlow)" />
      <rect x="45" y="18" width="150" height="118" rx="40" fill="url(#eBubble)" />
      <path d="M118 130 150 176 160 128Z" fill="#6c7bff" />
      <text x="120" y="102" textAnchor="middle" fontSize="84" fontWeight="800" fill="#fff" fontFamily="Lato, system-ui, sans-serif">?</text>
      <g stroke="#7fb0ff" strokeWidth="5" strokeLinecap="round"><path d="M222 38 232 20" /><path d="M240 58 262 50" /><path d="M236 36 250 30" /></g>
      <g transform="translate(18 186) rotate(-8)">
        <rect width="118" height="148" rx="12" fill="#fff" stroke="#cfe3f5" strokeWidth="2" />
        <g stroke="#b9d3f0" strokeWidth="6" strokeLinecap="round"><path d="M70 28h28" /><path d="M70 46h28" /><path d="M20 98h78" /><path d="M20 116h60" /></g>
        <rect x="-16" y="42" width="78" height="32" rx="9" fill="#ff5b78" />
        <text x="23" y="65" textAnchor="middle" fontSize="19" fontWeight="800" fill="#fff" fontFamily="Lato, system-ui, sans-serif">PDF</text>
      </g>
      <g transform="translate(118 322) rotate(-6)"><rect width="62" height="76" rx="8" fill="#fff" stroke="#cfe3f5" strokeWidth="2" /><g stroke="#b9d3f0" strokeWidth="5" strokeLinecap="round"><path d="M12 20h38" /><path d="M12 36h38" /><path d="M12 52h26" /></g></g>
      <g transform="translate(352 262) rotate(9)"><rect width="64" height="84" rx="8" fill="#fff" stroke="#cfe3f5" strokeWidth="2" /><g stroke="#b9d3f0" strokeWidth="5" strokeLinecap="round"><path d="M12 20h40" /><path d="M12 36h40" /><path d="M12 52h28" /></g></g>
      <rect x="188" y="290" width="136" height="98" rx="48" fill="url(#eWhite)" />
      <rect x="236" y="318" width="40" height="8" rx="4" fill="#8aa0ff" opacity=".6" />
      <rect x="146" y="196" width="26" height="54" rx="13" fill="#5b8cff" /><rect x="340" y="196" width="26" height="54" rx="13" fill="#5b8cff" />
      <rect x="160" y="148" width="192" height="146" rx="64" fill="url(#eWhite)" />
      <rect x="178" y="168" width="156" height="102" rx="46" fill="url(#eScreen)" />
      <g stroke="#7fe8ff" strokeWidth="9" strokeLinecap="round" fill="none"><path d="M212 216q15-20 30 0" /><path d="M270 216q15-20 30 0" /><path d="M240 240q16 15 32 0" /></g>
      <ellipse cx="204" cy="240" rx="9" ry="5" fill="#ff8fb4" opacity=".55" /><ellipse cx="308" cy="240" rx="9" ry="5" fill="#ff8fb4" opacity=".55" />
      <path d="M200 350q-38-8-14-58" stroke="url(#eWhite)" strokeWidth="30" strokeLinecap="round" fill="none" />
      <circle cx="196" cy="284" r="19" fill="#fff" />
      <path d="M194 268q-2-14 8-14" stroke="#dfe5ff" strokeWidth="9" strokeLinecap="round" fill="none" />
    </svg>
  );
}
