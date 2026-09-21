type SourceClass = 'measured' | 'independent' | 'provider' | 'cross-provider';
type Family = 'local' | 'claude' | 'gpt';

interface Observation {
  id: string;
  condition: string;
  model: string;
  score: number;
  scoreLabel: string;
  evidence: string;
  sourceClass: SourceClass;
  family: Family;
  sourceUrl: string;
}

const SOURCE_LABELS: Record<SourceClass, string> = {
  measured: 'Measured here',
  independent: 'Epoch independent',
  provider: 'Provider-reported',
  'cross-provider': 'Cross-provider',
};

const text = (element: Element | null): string => element?.textContent?.trim() ?? '';

function sourceClass(condition: string, evidence: string): SourceClass {
  if (/measured here/i.test(evidence)) return 'measured';
  if (/epoch independent/i.test(condition)) return 'independent';
  if (/cross-provider/i.test(`${condition} ${evidence}`)) return 'cross-provider';
  return 'provider';
}

function modelFamily(condition: string): Family {
  if (/^claude /i.test(condition)) return 'claude';
  if (/^gpt-/i.test(condition)) return 'gpt';
  return 'local';
}

function modelName(condition: string): string {
  return condition
    .replace(/ · local LM Studio · medium effort$/i, '')
    .replace(/ · (?:Anthropic provider|Epoch independent|OpenAI cross-provider)$/i, '');
}

function readObservations(root: HTMLElement): Observation[] {
  const table = root.closest('section')?.querySelector('table');
  if (!table) return [];
  const fallbackUrl = root.dataset.sourcesUrl ?? '../sources/';
  return [...table.querySelectorAll<HTMLTableRowElement>('tbody tr')]
    .map((row, index) => {
      const [modelCell, scoreCell, evidenceCell] = [...row.cells];
      const condition = text(modelCell ?? null);
      const scoreLabel = text(scoreCell ?? null);
      const score = Number.parseFloat(scoreLabel.replace('%', ''));
      const evidence = text(evidenceCell ?? null);
      if (!condition || !Number.isFinite(score) || !evidence) return null;
      const link = modelCell?.querySelector<HTMLAnchorElement>('a');
      return {
        id: `gpqa-observation-${index + 1}`,
        condition,
        model: modelName(condition),
        score,
        scoreLabel,
        evidence,
        sourceClass: sourceClass(condition, evidence),
        family: modelFamily(condition),
        sourceUrl: link?.href ?? fallbackUrl,
      } satisfies Observation;
    })
    .filter((record): record is Observation => record !== null);
}

function addSelect(
  controls: HTMLElement,
  name: string,
  labelText: string,
  options: Array<[string, string]>,
  selected: string,
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
    option.selected = value === selected;
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
  eyebrow.textContent = 'Selected observation';
  const title = document.createElement('h4');
  title.textContent = record.condition;
  const score = document.createElement('strong');
  score.className = 'explorer-detail-score';
  score.textContent = record.scoreLabel;
  score.setAttribute('aria-label', `Reported score ${record.scoreLabel}`);
  const facts = document.createElement('dl');
  facts.append(
    detailItem('Evidence', SOURCE_LABELS[record.sourceClass]),
    detailItem('Published condition', record.evidence),
    detailItem(
      'Comparison',
      record.sourceClass === 'measured'
        ? 'Measured locally here; external protocols are not matched.'
        : 'Directional context—not protocol matched to the local run.',
    ),
  );
  const link = document.createElement('a');
  link.href = record.sourceUrl;
  link.textContent = record.sourceClass === 'measured' ? 'Review the evidence record' : 'Open the cited source';
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
  controls.replaceChildren();
  addSelect(
    controls,
    'gpqa-view',
    'Show',
    [
      ['overview', 'All GPT + independent Claude'],
      ['all', 'All published observations'],
    ],
    'overview',
  );
  addSelect(
    controls,
    'gpqa-family',
    'Models',
    [
      ['all', 'All models'],
      ['claude', 'Claude'],
      ['gpt', 'GPT'],
    ],
    'all',
  );
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
  list.setAttribute('aria-label', 'Reported GPQA Diamond observations');
  plot.replaceChildren(status, scale, list);

  let selectedId = observations[0].id;

  const render = (): void => {
    const view = controls.querySelector<HTMLSelectElement>('select[name="gpqa-view"]')?.value;
    const family = controls.querySelector<HTMLSelectElement>('select[name="gpqa-family"]')?.value;
    const visible = observations.filter((record) => {
      const overview =
        view === 'all' ||
        record.sourceClass === 'measured' ||
        record.family === 'gpt' ||
        (record.family === 'claude' && record.sourceClass === 'independent');
      const inFamily = family === 'all' || record.family === 'local' || record.family === family;
      return overview && inFamily;
    });
    if (!visible.some((record) => record.id === selectedId)) selectedId = visible[0]?.id ?? '';
    const scope =
      view === 'all'
        ? 'Every source-labeled observation is shown.'
        : 'This view shows all GPT reports and one independent Epoch score for each Claude model.';
    status.textContent = `${scope} Showing ${visible.length} of ${observations.length}. Splash remains pinned; rows are not ranked.`;
    list.replaceChildren();

    for (const record of visible) {
      const item = document.createElement('li');
      item.dataset.sourceClass = record.sourceClass;
      if (record.sourceClass === 'measured') item.className = 'local-reference';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'explorer-observation';
      button.dataset.sourceClass = record.sourceClass;
      button.setAttribute('aria-pressed', String(record.id === selectedId));
      button.setAttribute(
        'aria-label',
        `${record.condition}: ${record.scoreLabel}, ${SOURCE_LABELS[record.sourceClass]}. Select for evidence details.`,
      );

      const name = document.createElement('span');
      name.className = 'explorer-model';
      name.textContent = record.model;
      const track = document.createElement('span');
      track.className = 'explorer-score-track';
      track.setAttribute('aria-hidden', 'true');
      const dot = document.createElement('span');
      dot.className = 'explorer-score-dot';
      dot.style.setProperty('--score', String(record.score));
      track.append(dot);
      const value = document.createElement('span');
      value.className = 'explorer-score';
      value.textContent = record.scoreLabel;
      const badge = document.createElement('span');
      badge.className = 'explorer-source';
      badge.textContent = SOURCE_LABELS[record.sourceClass];
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
  for (const root of document.querySelectorAll<HTMLElement>('[data-gpqa-explorer]')) {
    initializeExplorer(root);
  }
}

document.addEventListener('astro:page-load', initializeAllExplorers);
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeAllExplorers, { once: true });
} else {
  initializeAllExplorers();
}
