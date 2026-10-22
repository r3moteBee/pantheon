// Shared content-column widths (UI review #16). The chat message list and
// the composer both use CHAT_COLUMN so their left/right edges line up;
// `.content-column` is defined once in index.css.
//
//   <div className={CHAT_COLUMN}>…messages…</div>
//   <div className={CHAT_COLUMN}>…composer row…</div>
//
// Put the horizontal gutter (CHAT_GUTTER) on the same element in both places,
// never on one of them only, or the edges drift apart again.
export const CHAT_COLUMN = 'content-column'
export const CHAT_COLUMN_MAX = '56rem'   // = Tailwind max-w-4xl (896px)
export const CHAT_GUTTER = 'px-4'
