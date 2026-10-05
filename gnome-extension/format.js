// Pure formatting for the AI Usage indicator: no GNOME Shell imports, so it is testable on its own.
// Input is the parsed output of `ai-usage --json`.

export const COLOR = {
    claude: '#d97757',
    codex: '#412991',
    openrouter: '#2dbe7f',
    deepseek: '#4d6bfe',
    dim: '#9a9aa8',
    text: '#e6e6f0',
    crit: '#ff5555',
};

export const ICON = {
    claude: '\u2733',
    codex: '\u2b22',
    openrouter: '\u21c4',
    deepseek: '\u2b24',
};

export const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export function bar(pct) {
    const cells = Math.max(0, Math.min(10, Math.round(pct / 10)));
    return '\u2593'.repeat(cells) + '\u2591'.repeat(10 - cells);
}

// Returns { blocks: [{ markup }], worst, alert } — one popup item per provider.
export function renderLines(data) {
    const blocks = [];
    let worst = 0;
    let alert = false;

    for (const d of data) {
        const lines = [];
        let head = `${ICON[d.key] ?? '\u25cf'}  <b>${esc(d.name)}</b>`;
        if (d.sub)
            head += ` (${esc(d.sub)})`;
        if (d.stale)
            head += ' <i>stale</i>';
        lines.push(`<span foreground="${COLOR[d.key] ?? COLOR.text}">${head}</span>`);

        if (d.error) {
            lines.push(`  <span foreground="${COLOR.crit}">${esc(d.error)}</span>`);
        } else {
            for (const r of d.rows) {
                if (r.pct === null) {
                    lines.push(`<span foreground="${COLOR.dim}">${esc(r.label.padEnd(8))}</span>${esc(r.note)}`);
                } else {
                    const pct = Math.round(r.pct);
                    worst = Math.max(worst, pct);
                    lines.push(`<span foreground="${COLOR.dim}">${esc(r.label.padEnd(8))}</span>`
                        + `<span foreground="${pct >= 90 ? COLOR.crit : COLOR[d.key] ?? COLOR.text}">${bar(pct)}</span>`
                        + ` ${pct}% used  ${esc(r.note)}`);
                }
            }
        }
        if (d.reached)
            alert = true;
        blocks.push({ markup: lines.join('\n') });
    }

    return { blocks, worst, alert: alert || worst >= 90 };
}
