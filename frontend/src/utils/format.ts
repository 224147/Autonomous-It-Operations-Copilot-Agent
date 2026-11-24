const rtf = typeof Intl !== "undefined" && "RelativeTimeFormat" in Intl ? new Intl.RelativeTimeFormat("en", { numeric: "auto" }) : null;

const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [["day", 86400], ["hour", 3600], ["minute", 60], ["second", 1]];

export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "-";
  const diffSec = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(diffSec);
  for (const [unit, seconds] of UNITS) {
    if (abs >= seconds || unit === "second") {
      const value = Math.round(diffSec / seconds);
      return rtf ? rtf.format(value, unit) : `${Math.abs(value)} ${unit}s ${value < 0 ? "ago" : "from now"}`;
    }
  }
  return iso;
}

export function formatDateTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "-";
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "-";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

export function percent(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? "-" : `${(value * 100).toFixed(digits)}%`;
}

export function humanize(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
