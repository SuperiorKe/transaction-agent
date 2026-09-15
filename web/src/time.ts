/** "5 s ago", "3 min ago", "2 h ago". A server clock slightly ahead of the browser reads "just now". */
export function formatAgo(at: string, now: number): string {
  const then = Date.parse(at);
  if (Number.isNaN(then)) return "";

  const seconds = Math.floor((now - then) / 1000);
  if (seconds <= 0) return "just now";
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ago`;
}
