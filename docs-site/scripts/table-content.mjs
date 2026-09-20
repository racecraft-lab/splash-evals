// Keep wide Markdown tables readable without changing their native table semantics.
export function wrapWideTables(markdown) {
  return markdown.replace(/(?:^\|.+\|\r?\n)+/gm, (table) => {
    const columns = table.split('\n')[0].split('|').length - 2;
    if (columns <= 2) return table;
    return `<p class="table-hint">More columns to the right: scroll this table horizontally on small screens.</p>\n\n<div class="table-scroll" role="region" aria-label="Data table with horizontal scrolling" tabindex="0">\n\n${table}\n</div>\n`;
  });
}
