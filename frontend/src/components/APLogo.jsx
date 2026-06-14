/**
 * AlphaPartner Logo — self-contained inline SVG component.
 * No external CDN dependency. Renders identically in all contexts.
 */
export default function APLogo({ size = 32, className = "" }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 512 512"
      className={className}
      aria-label="AlphaPartner logo"
      role="img"
    >
      <defs>
        <linearGradient id="ap-logo-bg" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#09090B" />
          <stop offset="100%" stopColor="#18181B" />
        </linearGradient>
        <linearGradient id="ap-logo-accent" x1="0%" y1="100%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#4F46E5" />
          <stop offset="100%" stopColor="#818CF8" />
        </linearGradient>
      </defs>
      {/* Background */}
      <rect width="512" height="512" rx="96" fill="url(#ap-logo-bg)" />
      {/* Border ring */}
      <rect width="508" height="508" x="2" y="2" rx="94" fill="none" stroke="#6366F1" strokeWidth="2" strokeOpacity="0.4" />
      {/* Rising bar chart */}
      <rect x="80"  y="340" width="44" height="96"  rx="6" fill="url(#ap-logo-accent)" opacity="0.5" />
      <rect x="140" y="300" width="44" height="136" rx="6" fill="url(#ap-logo-accent)" opacity="0.65" />
      <rect x="200" y="260" width="44" height="176" rx="6" fill="url(#ap-logo-accent)" opacity="0.75" />
      <rect x="260" y="210" width="44" height="226" rx="6" fill="url(#ap-logo-accent)" opacity="0.85" />
      <rect x="320" y="160" width="44" height="276" rx="6" fill="url(#ap-logo-accent)" />
      <rect x="380" y="120" width="44" height="316" rx="6" fill="url(#ap-logo-accent)" />
      {/* Trend line */}
      <polyline
        points="102,340 162,300 222,258 282,208 342,158 402,118"
        stroke="#818CF8"
        strokeWidth="5"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.9"
      />
      {/* Top data point highlight */}
      <circle cx="402" cy="118" r="9" fill="#A5B4FC" />
    </svg>
  );
}
