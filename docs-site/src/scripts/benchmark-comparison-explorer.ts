interface Observation {
  id: string;
  condition: string;
  score: number | null;
  scoreLabel: string;
  evidence: string;
  publishedCondition: string;
  sourceClass: string;
  sourceLabel: string;
  family: string;
  sourceUrl: string;
  local: boolean;
}

const text = (element: Element | null): string => element?.textContent?.trim() ?? '';

function readObservations(root: HTMLElement): Observation[] {
  const table = root.querySelector<HTMLElement>('[data-benchmark-table]');
  if (!table) return [];
  return [...table.querySelectorAll<HTMLTableRowElement>('tbody tr')]
    .map((row, index) => {
      const [modelCell, scoreCell, evidenceCell, conditionCell] = [...row.cells];
      const condition = text(modelCell ?? null);
      const rawScore = row.dataset.score ?? '';
      const parsedScore = rawScore === '' ? null : Number.parseFloat(rawScore);
      if (!condition || (parsedScore !== null && !Number.isFinite(parsedScore))) return null;
      return {
        id: `benchmark-observation-${index + 1}`,
        condition,
        score: parsedScore,
        scoreLabel: text(scoreCell ?? null),
        evidence: text(evidenceCell ?? null),
        publishedCondition: text(conditionCell ?? null),
        sourceClass: row.dataset.sourceClass ?? 'published',
        sourceLabel: text(evidenceCell ?? null),
        family: row.dataset.family ?? 'other',
        sourceUrl: row.dataset.sourceUrl ?? '../sources/',
        local: row.dataset.local === 'true',
      } satisfies Observation;
    })
    .filter((record): record is Observation => record !== null);
}

function addSelect(
  controls: HTMLElement,
  name: string,
  labelText: string,
  options: Array<[string, string]>,
): void {
  const label = document.createElement('label');
  label.className = 'explorer-filter';
  const labelCopy = document.createElement('span');
  labelCopy.textContent = labelText;
  const select = document.createElement('select');
  select.name = name;
  for (const [value, optionText] of options) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = optionText;
    select.append(option);
  }
  label.append(labelCopy, select);
  controls.append(label);
}

function detailItem(term: string, description: string): HTMLDivElement {
  const item = document.createElement('div');
  const dt = document.createElement('dt');
  const dd = document.createElement('dd');
  dt.textContent = term;
  dd.textContent = description;
  item.append(dt, dd);
  return item;
}

function showDetail(detail: HTMLElement, record: Observation): void {
  detail.replaceChildren();
  detail.hidden = false;
  const eyebrow = document.createElement('p');
  eyebrow.className = 'explorer-detail-eyebrow';
  eyebrow.textContent = record.local ? 'Local evidence status' : 'Selected external observation';
  const title = document.createElement('h4');
  title.textContent = record.condition;
  const score = document.createElement('strong');
  score.className = 'explorer-detail-score';
  score.textContent = record.scoreLabel;
  score.setAttribute('aria-label', record.score === null ? 'No local score published' : `Reported score ${record.scoreLabel}`);
  const facts = document.createElement('dl');
  facts.append(
    detailItem('Evidence', record.sourceLabel),
    detailItem('Published condition', record.publishedCondition),
    detailItem(
      'Comparison',
      record.score === null
        ? 'Pending is not zero. No local capability result is claimed.'
        : record.local
          ? 'Measured locally here; external protocols are not matched.'
          : 'Directional context—not protocol matched to the local run.',
    ),
  );
  const link = document.createElement('a');
  link.href = record.sourceUrl;
  link.textContent = record.local ? 'Open the local evidence status' : 'Open the cited source';
  if (/^https?:/.test(record.sourceUrl)) link.rel = 'noreferrer';
  detail.append(eyebrow, title, score, facts, link);
}

function initializeExplorer(root: HTMLElement): void {
  if (root.dataset.enhanced === 'true') return;
  const observations = readObservations(root);
  const controls = root.querySelector<HTMLElement>('[data-explorer-controls]');
  const plot = root.querySelector<HTMLElement>('[data-explorer-plot]');
  const detail = root.querySelector<HTMLElement>('[data-explorer-detail]');
  if (!controls || !plot || !detail || observations.length === 0) return;

  root.dataset.enhanced = 'true';
  const sourceClasses = [...new Set(observations.filter((item) => !item.local).map((item) => item.sourceClass))];
  const families = [...new Set(observations.filter((item) => !item.local).map((item) => item.family))];
  addSelect(controls, 'benchmark-source', 'Evidence', [
    ['all', 'All source classes'],
    ...sourceClasses.map((value) => [value, observations.find((item) => item.sourceClass === value)?.sourceLabel ?? value] as [string, string]),
  ]);
  addSelect(controls, 'benchmark-family', 'Models', [
    ['all', 'All model families'],
    ...families.map((value) => [value, value === 'gpt' ? 'GPT / OpenAI' : `${value[0].toUpperCase()}${value.slice(1)}`] as [string, string]),
  ]);
  controls.hidden = false;

  const status = document.createElement('p');
  status.className = 'explorer-status';
  status.setAttribute('aria-live', 'polite');
  const scale = document.createElement('div');
  scale.className = 'explorer-scale';
  scale.setAttribute('aria-hidden', 'true');
  for (const value of [0, 25, 50, 75, 100]) {
    const tick = document.createElement('span');
    tick.textContent = String(value);
    scale.append(tick);
  }
  const list = document.createElement('ol');
  list.className = 'explorer-list';
  list.setAttribute('aria-label', `${root.dataset.benchmarkLabel ?? 'Benchmark'} source-labeled observations`);
  plot.replaceChildren(status, scale, list);

  let selectedId = observations[0].id;
  const render = (): void => {
    const selectedSource = controls.querySelector<HTMLSelectElement>('select[name="benchmark-source"]')?.value ?? 'all';
    const selectedFamily = controls.querySelector<HTMLSelectElement>('select[name="benchmark-family"]')?.value ?? 'all';
    const visible = observations.filter((record) =>
      record.local ||
      ((selectedSource === 'all' || record.sourceClass === selectedSource) &&
        (selectedFamily === 'all' || record.family === selectedFamily)),
    );
    if (!visible.some((record) => record.id === selectedId)) selectedId = visible[0]?.id ?? '';
    status.textContent = `Showing ${visible.length - 1} external observations plus the pinned local status. Rows are not ranked.`;
    list.replaceChildren();

    for (const record of visible) {
      const item = document.createElement('li');
      item.dataset.sourceClass = record.sourceClass;
      if (record.local) item.className = 'local-reference';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'explorer-observation';
      button.dataset.sourceClass = record.sourceClass;
      button.setAttribute('aria-pressed', String(record.id === selectedId));
      button.setAttribute('aria-label', `${record.condition}: ${record.scoreLabel}, ${record.sourceLabel}. Select for evidence details.`);

      const name = document.createElement('span');
      name.className = 'explorer-model';
      name.textContent = record.condition;
      const track = document.createElement('span');
      track.className = 'explorer-score-track';
      track.setAttribute('aria-hidden', 'true');
      if (record.score !== null) {
        const dot = document.createElement('span');
        dot.className = 'explorer-score-dot';
        dot.style.setProperty('--score', String(record.score));
        track.append(dot);
      } else {
        track.classList.add('pending');
      }
      const value = document.createElement('span');
      value.className = 'explorer-score';
      value.textContent = record.scoreLabel;
      const badge = document.createElement('span');
      badge.className = 'explorer-source';
      badge.textContent = record.sourceLabel;
      button.append(name, track, value, badge);
      const select = (): void => {
        selectedId = record.id;
        for (const candidate of list.querySelectorAll<HTMLButtonElement>('.explorer-observation')) {
          candidate.setAttribute('aria-pressed', String(candidate === button));
        }
        showDetail(detail, record);
      };
      button.addEventListener('click', select);
      button.addEventListener('focus', select);
      item.append(button);
      list.append(item);
    }

    const selected = visible.find((record) => record.id === selectedId) ?? visible[0];
    if (selected) showDetail(detail, selected);
  };

  controls.addEventListener('change', render);
  render();
}

function initializeAllExplorers(): void {
  for (const root of document.querySelectorAll<HTMLElement>('[data-benchmark-explorer]')) {
    initializeExplorer(root);
  }
}

document.addEventListener('astro:page-load', initializeAllExplorers);
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeAllExplorers, { once: true });
} else {
  initializeAllExplorers();
}
