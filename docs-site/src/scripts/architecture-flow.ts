const STEP_DELAY_MS = 700;

function initializeArchitectureFlow(root: HTMLElement): void {
  if (root.dataset.enhanced === 'true') return;

  const steps = [...root.querySelectorAll<HTMLElement>('[data-flow-step]')];
  const triggers = steps.map((step) => step.querySelector<HTMLButtonElement>('[data-flow-trigger]'));
  const panels = [...root.querySelectorAll<HTMLElement>('[data-flow-detail]')];
  const controls = root.querySelector<HTMLElement>('[data-flow-controls]');
  const play = root.querySelector<HTMLButtonElement>('[data-flow-play]');
  const reset = root.querySelector<HTMLButtonElement>('[data-flow-reset]');
  const status = root.querySelector<HTMLElement>('[data-flow-status]');
  if (
    steps.length === 0 ||
    panels.length !== steps.length ||
    !controls ||
    !play ||
    !reset ||
    !status ||
    triggers.some((item) => !item) ||
    panels.some((item) => !item)
  )
    return;

  const statusElement = status;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let activeIndex = 0;
  let timer: number | undefined;
  let playing = false;

  function stop(): void {
    if (timer !== undefined) window.clearTimeout(timer);
    timer = undefined;
    playing = false;
    play!.textContent = 'Play request';
    play!.setAttribute('aria-pressed', 'false');
  }

  function show(index: number, announce = true): void {
    activeIndex = index;
    steps.forEach((step, stepIndex) => {
      const trigger = triggers[stepIndex]!;
      const panel = panels[stepIndex]!;
      const active = stepIndex === index;
      step.dataset.state = stepIndex < index ? 'complete' : active ? 'active' : 'upcoming';
      trigger.setAttribute('aria-expanded', String(active));
      if (active) trigger.setAttribute('aria-current', 'step');
      else trigger.removeAttribute('aria-current');
      panel.hidden = !active;
    });
    if (!reduceMotion) {
      triggers[index]!.animate(
        [
          { opacity: 0.72, transform: 'translateY(4px)' },
          { opacity: 1, transform: 'translateY(0)' },
        ],
        { duration: 260, easing: 'cubic-bezier(.2,.8,.2,1)' },
      );
    }
    if (announce)
      statusElement.textContent = `Step ${index + 1} of ${steps.length}: ${triggers[index]!.dataset.stepLabel}.`;
  }

  function advance(): void {
    if (activeIndex >= steps.length - 1) {
      stop();
      statusElement.textContent =
        'Request path complete. The reviewed result moves to the evidence boundary below.';
      return;
    }
    show(activeIndex + 1);
    timer = window.setTimeout(advance, reduceMotion ? 0 : STEP_DELAY_MS);
  }

  play.addEventListener('click', () => {
    if (playing) {
      stop();
      statusElement.textContent = `Trace paused at step ${activeIndex + 1}.`;
      return;
    }
    if (activeIndex === steps.length - 1) show(0, false);
    playing = true;
    play.textContent = 'Pause trace';
    play.setAttribute('aria-pressed', 'true');
    statusElement.textContent = 'Tracing the local request path.';
    timer = window.setTimeout(advance, reduceMotion ? 0 : STEP_DELAY_MS);
  });

  reset.addEventListener('click', () => {
    stop();
    show(0, false);
    statusElement.textContent = 'Trace reset to the evaluation runner.';
    triggers[0]!.focus();
  });

  triggers.forEach((trigger, index) => {
    trigger!.addEventListener('click', () => {
      stop();
      show(index);
    });
  });

  root.dataset.enhanced = 'true';
  controls.hidden = false;
  show(0, false);
}

document.querySelectorAll<HTMLElement>('[data-architecture-flow]').forEach(initializeArchitectureFlow);
