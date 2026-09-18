/** "23,000" -- KES amounts are always written with the thousands separator, never a currency symbol
 * (the "KES" label is added at each call site, next to whichever number it's naming). */
export function formatKes(value: number): string {
  return new Intl.NumberFormat("en-KE").format(value);
}
