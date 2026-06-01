const ESC = '\x1b'
const BEL = '\x07'
const ST = `${ESC}\\`

export const OSC52_CLIPBOARD_QUERY = `${ESC}]52;c;?${BEL}`

type OscResponse = { code: number; data: string; type: 'osc' }

type OscQuerier = {
  flush: () => Promise<void>
  send: <T>(query: { match: (r: unknown) => r is T; request: string }) => Promise<T | undefined>
}

function wrapForMultiplexer(sequence: string): string {
  if (process.env['TMUX']) {
    return `${ESC}Ptmux;${sequence.split(ESC).join(ESC + ESC)}${ST}`
  }

  if (process.env['STY']) {
    return `${ESC}P${sequence}${ST}`
  }

  return sequence
}

export function buildOsc52ClipboardQuery(): string {
  return wrapForMultiplexer(OSC52_CLIPBOARD_QUERY)
}

export function parseOsc52ClipboardData(data: string): null | string {
  const firstSep = data.indexOf(';')

  if (firstSep === -1) {
    return null
  }

  const selection = data.slice(0, firstSep)
  const payload = data.slice(firstSep + 1)

  if ((selection !== 'c' && selection !== 'p') || !payload || payload === '?') {
    return null
  }

  try {
    return Buffer.from(payload, 'base64').toString('utf8')
  } catch {
    return null
  }
}

export async function readOsc52Clipboard(querier: null | OscQuerier, timeoutMs = 500): Promise<null | string> {
  if (!querier) {
    return null
  }

  const timeout = new Promise<undefined>(resolve => setTimeout(resolve, timeoutMs))

  const query = querier.send<OscResponse>({
    request: buildOsc52ClipboardQuery(),
    match: (r: unknown): r is OscResponse => {
      return !!r && typeof r === 'object' && (r as OscResponse).type === 'osc' && (r as OscResponse).code === 52
    }
  })

  const response = await Promise.race([query, timeout])

  await querier.flush()

  return response ? parseOsc52ClipboardData(response.data) : null
}

// Gap-audit 2026-06-01 (JS-GW-04): this is the one path that writes raw
// terminal-control bytes straight to stdout, outside the Ink scrubber. Assert
// the encoded payload is strict base64 before emitting the OSC 52 frame so a
// future regression in the encoder can never let unescaped control bytes (a
// premature BEL/ST, a nested ESC) terminate the frame early and inject a
// second control sequence into the terminal.
const BASE64_RE = /^[A-Za-z0-9+/]*={0,2}$/

export const writeOsc52Clipboard = (s: string) => {
  const payload = Buffer.from(s, 'utf8').toString('base64')

  if (!BASE64_RE.test(payload)) {
    // Should be unreachable (toString('base64') is canonical), but fail closed
    // rather than write a control sequence we cannot vouch for.
    return false
  }

  return process.stdout.write(`\x1b]52;c;${payload}\x07`)
}
