/**
 * One small icon per nav destination, inline rather than a dependency.
 *
 * This project has no icon library (see package.json), and a plain-text
 * sidebar is the single biggest thing making the console read as an
 * unstyled admin panel rather than a considered product. A handful of
 * hand-picked outline glyphs (20x20, currentColor, 1.5 stroke) closes most
 * of that gap without a new dependency to keep updated for a dozen glyphs.
 * Purely decorative (`aria-hidden`): the link text is still what a screen
 * reader announces.
 */

import type { SVGProps } from "react";

function Icon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-4 w-4 shrink-0"
      {...props}
    />
  );
}

export function HomeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M3 9.5 10 4l7 5.5M5 8.5V16a1 1 0 0 0 1 1h3v-4.5h2V17h3a1 1 0 0 0 1-1V8.5" />
    </Icon>
  );
}

export function DatasetIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 4.5A1.5 1.5 0 0 1 5.5 3h6l4 4v9.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 3.5 17z" />
      <path d="M11.5 3v3.5a1 1 0 0 0 1 1H16M7 11h6M7 13.5h6" />
    </Icon>
  );
}

export function ShieldCheckIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M10 2.5 4 4.8v4.7c0 4 2.6 6.9 6 7.9 3.4-1 6-3.9 6-7.9V4.8z" />
      <path d="m7.5 10 1.8 1.8 3.2-3.6" />
    </Icon>
  );
}

export function AgentIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <rect x="5" y="6.5" width="10" height="8" rx="1.5" />
      <path d="M10 6.5V4M7.5 4h5M7.5 10h.01M12.5 10h.01M3.5 9v3M16.5 9v3" />
    </Icon>
  );
}

export function PipelineIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx="4.5" cy="5" r="1.75" />
      <circle cx="4.5" cy="15" r="1.75" />
      <circle cx="15.5" cy="10" r="1.75" />
      <path d="M6.2 5.7 13.9 9M6.2 14.3 13.9 11" />
    </Icon>
  );
}

export function RunsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 5.5h12M4 10h12M4 14.5h7" />
      <circle cx="16" cy="14.5" r="1.4" />
    </Icon>
  );
}

export function EgressIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx="10" cy="10" r="7" />
      <path d="M3 10h14M10 3c1.8 2 2.8 4.5 2.8 7s-1 5-2.8 7c-1.8-2-2.8-4.5-2.8-7s1-5 2.8-7Z" />
    </Icon>
  );
}

export function ServicesIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <rect x="4" y="4" width="12" height="4.5" rx="1" />
      <rect x="4" y="11.5" width="12" height="4.5" rx="1" />
      <path d="M6.5 6.25h.01M6.5 13.75h.01" />
    </Icon>
  );
}

export function HousekeepingIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="4" width="13" height="3.5" rx="1" />
      <path d="M4.5 7.5V15a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1V7.5M8 10.5h4" />
    </Icon>
  );
}

export function PeopleIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx="7" cy="7" r="2.25" />
      <path d="M2.75 16c.4-2.8 2.2-4.5 4.25-4.5s3.85 1.7 4.25 4.5" />
      <circle cx="14.5" cy="7.5" r="1.9" />
      <path d="M12.5 11.7c1.6.2 3 1.7 3.3 4.3h1.45" />
    </Icon>
  );
}

export function KeyIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx="6.5" cy="10" r="3" />
      <path d="M9.2 10h7.3M13.5 10v3M16 10v2" />
    </Icon>
  );
}

export function AuditIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M2.5 10S5 4.5 10 4.5 17.5 10 17.5 10 15 15.5 10 15.5 2.5 10 2.5 10Z" />
      <circle cx="10" cy="10" r="2.25" />
    </Icon>
  );
}
