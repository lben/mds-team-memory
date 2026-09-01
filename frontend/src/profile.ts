/** One rule for turning a profile label into avatar initials.
 *
 * Deriving this in three places produced three different answers — a display
 * name of "Benito" rendered as "NI" because one caller sliced the last four
 * characters, which is only meaningful for an anonymous "Browser profile XXXX".
 */
export function initialsFor(label: string | null | undefined): string {
  const trimmed = (label ?? '').trim()
  if (!trimmed) return 'BP'
  const anonymous = trimmed.match(/^Browser profile\s+(\S+)$/i)
  if (anonymous) return anonymous[1].slice(0, 2).toUpperCase()
  return (
    trimmed
      .split(/\s+/)
      .filter(Boolean)
      .map((word) => word[0])
      .slice(0, 2)
      .join('')
      .toUpperCase() || 'BP'
  )
}

/** How a profile's identity should be described.
 *
 * An account is the only thing that survives a cookie clear, so it is the
 * distinction worth showing: signed in, a self-declared name on this machine
 * only, or nothing at all.
 */
export function identityNote(label: string | null | undefined, verified = false): string {
  if (verified) return 'Signed in · your work is safe'
  const trimmed = (label ?? '').trim()
  return /^Browser profile\s+\S+$/i.test(trimmed)
    ? 'This browser only · no account'
    : 'Name on this machine only · no account'
}
