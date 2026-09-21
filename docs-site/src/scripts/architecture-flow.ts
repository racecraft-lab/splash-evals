const STEP_DELAY_MS = 8_000;

function alignRequestRoutes(root: HTMLElement, steps: HTMLElement[]): void {
  const map = root.querySelector<HTMLElement>('.request-map');
  const svg = root.querySelector<SVGElement>('.request-routes');
  if (!map || !svg) return;
  const observer = new ResizeObserver(() => {
    const origin = map.getBoundingClientRect();
    svg.setAttribute('viewBox', `0 0 ${origin.width} ${origin.height}`);
    const boxes = steps.map((step) => step.getBoundingClientRect());
    boxes.forEach((from, index) => {
      const to = boxes[(index + 1) % boxes.length];
      const horizontal = Math.abs(from.top - to.top) < 2;
      const direction = horizontal ? Math.sign(to.left - from.left) : Math.sign(to.top - from.top);
      const x1 = (horizontal ? (direction > 0 ? from.right : from.left) + direction * 7 : from.left + from.width / 2) - origin.left;
      const y1 = (horizontal ? from.top + from.height / 2 : (direction > 0 ? from.bottom : from.top) + direction * 7) - origin.top;
      const x2 = (horizontal ? (direction > 0 ? to.left : to.right) - direction * 9 : to.left + to.width / 2) - origin.left;
      const y2 = (horizontal ? to.top + to.height / 2 : (direction > 0 ? to.top : to.bottom) - direction * 9) - origin.top;
      root.querySelectorAll(`[data-flow-track="${index}"], [data-flow-route="${index}"]`).forEach((path) => {
        path.setAttribute('d', `M${x1} ${y1} L${x2} ${y2}`);
      });
    });
  });
  observer.observe(map);
}

function initializeArchitectureFlow(root: HTMLElement): void {
  if (root.dataset.enhanced === 'true') return;
  const steps = [...root.querySelectorAll<HTMLElement>('[data-flow-step]')];
  const triggers = [...root.querySelectorAll<HTMLButtonElement>('[data-flow-trigger]')];
  const panels = [...root.querySelectorAll<HTMLElement>('[data-flow-detail]')];
  const controls = root.querySelector<HTMLElement>('[data-flow-controls]');
  const play = root.querySelector<HTMLButtonElement>('[data-flow-play]');
  const next = root.querySelector<HTMLButtonElement>('[data-flow-next]');
  const reset = root.querySelector<HTMLButtonElement>('[data-flow-reset]');
  const status = root.querySelector<HTMLElement>('[data-flow-status]');
  if (!steps.length || triggers.length !== steps.length || panels.length !== steps.length ||
      !controls || !play || !next || !reset || !status) return;

  let activeIndex = 0;
  let timer: number | undefined;
  let playing = false;
  let remainingMs = STEP_DELAY_MS;
  let startedAt = 0;

  const stop = (): void => {
    if (playing) remainingMs = Math.max(0, remainingMs - (performance.now() - startedAt));
    window.clearTimeout(timer);
    timer = undefined;
    playing = false;
    root.dataset.playing = 'false';
    play.textContent = 'Play request';
  };

  const show = (index: number, announce = true): void => {
    activeIndex = index;
    remainingMs = STEP_DELAY_MS;
    startedAt = performance.now();
    root.dataset.stage = String(index);
    steps.forEach((step, stepIndex) => {
      const active = stepIndex === index;
      step.dataset.state = active ? 'active' : 'idle';
      triggers[stepIndex].setAttribute('aria-expanded', String(active));
      if (active) triggers[stepIndex].setAttribute('aria-current', 'step');
      else triggers[stepIndex].removeAttribute('aria-current');
      panels[stepIndex].hidden = !active;
    });
    // Restart the visual clock even when the reader reselects the current stage.
    const activeRoute = root.querySelector<SVGElement>(`[data-flow-route="${index}"]`);
    const animations = [...(activeRoute?.getAnimations() ?? []), ...triggers[index].getAnimations({ subtree: true })];
    animations.forEach((animation) => { animation.currentTime = 0; });
    if (announce) status.textContent = `Step ${index + 1} of ${steps.length}: ${triggers[index].dataset.stepLabel}.`;
  };

  const advance = (): void => {
    if (activeIndex === steps.length - 1) {
      stop();
      status.textContent = 'Sequence complete. The real run repeats this cycle for 198 questions.';
      return;
    }
    show(activeIndex + 1);
    timer = window.setTimeout(advance, STEP_DELAY_MS);
  };

  play.addEventListener('click', () => {
    if (playing) {
      stop();
      status.textContent = `Paused at step ${activeIndex + 1}. Select any stage to explore.`;
      return;
    }
    if (activeIndex === steps.length - 1) show(0);
    playing = true;
    startedAt = performance.now();
    root.dataset.playing = 'true';
    play.textContent = 'Pause';
    status.textContent = `Playing step ${activeIndex + 1} of ${steps.length} · 8 seconds per step.`;
    timer = window.setTimeout(advance, remainingMs);
  });
  next.addEventListener('click', () => {
    stop();
    show((activeIndex + 1) % steps.length);
  });
  reset.addEventListener('click', () => {
    stop();
    show(0);
    triggers[0].focus();
  });
  triggers.forEach((trigger, index) => {
    trigger.addEventListener('click', () => {
      stop();
      show(index);
    });
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && playing) {
      stop();
      status.textContent = `Paused at step ${activeIndex + 1} while this page was hidden.`;
    }
  });

  root.dataset.enhanced = 'true';
  alignRequestRoutes(root, steps);
  controls.hidden = false;
  show(0, false);
}

function initializeAllArchitectureFlows(): void {
  document.querySelectorAll<HTMLElement>('[data-architecture-flow]').forEach(initializeArchitectureFlow);
}

document.addEventListener('astro:page-load', initializeAllArchitectureFlows);
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeAllArchitectureFlows, { once: true });
} else {
  initializeAllArchitectureFlows();
}
