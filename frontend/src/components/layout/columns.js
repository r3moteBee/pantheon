// Shared reading-column widths (UI review #16). The chat transcript, the
// attachment tray and the composer use the SAME class string so the bubbles
// and the input line up at every viewport; parent containers add vertical
// padding only. Put the gutter on this element in every place, never on one
// of them only, or the edges drift apart again.
export const CHAT_COLUMN = 'mx-auto w-full max-w-3xl px-4'
export const CHAT_COLUMN_MAX = '48rem'   // = Tailwind max-w-3xl (768px)
export const CHAT_GUTTER = 'px-4'
