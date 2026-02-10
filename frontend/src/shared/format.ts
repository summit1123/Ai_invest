export function fmtTsKst(ts: string | null | undefined): string {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return String(ts)
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: '2-digit',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(d)
}

export function fmtNumber(n: number | null | undefined, digits: number = 2): string {
  if (n === null || n === undefined) return ''
  if (!Number.isFinite(n)) return String(n)
  return new Intl.NumberFormat('ko-KR', { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(n)
}

