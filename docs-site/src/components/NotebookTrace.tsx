// A vertical signal trace down the lab sheet. As the reader scrolls, the trace fills and
// the matching test point lights up. It is an overlay beside the content, never a wrapper,
// so the page's HTML stays plain Astro output. Without JS or with reduced motion, the page
// reads the same: every test point is shown with its number. Pages without test points get
// no trace.
import { motion, useReducedMotion, useScroll, useSpring, useTransform } from 'motion/react';
import { useEffect, useRef, useState } from 'react';

export default function NotebookTrace() {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  // Start static on server and first client render so the HTML matches; animate after mount.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const animate = mounted && !reduce;
  // The trace runs from the first test point to the last, never beside unnumbered text.
  const [span, setSpan] = useState({ start: 0, end: 0 });
  const spanRef = useRef({ start: 0, end: 0 });
  useEffect(() => {
    const root = ref.current?.closest<HTMLElement>('.trace-host');
    if (!root) return;
    const measure = () => {
      const points = root.querySelectorAll<HTMLElement>('[data-testpoint]');
      const center = (point: HTMLElement) =>
        point.getBoundingClientRect().top - root.getBoundingClientRect().top + point.offsetHeight / 2;
      spanRef.current =
        points.length > 1 ? { start: center(points[0]), end: center(points[points.length - 1]) } : { start: 0, end: 0 };
      setSpan(spanRef.current);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, []);
  // Fill to the reading line (45% down the viewport), measured along the trace, so the
  // fill meets each test point as that section reaches the reading line.
  const { scrollY } = useScroll();
  const progress = useTransform(scrollY, (y) => {
    const root = ref.current?.closest<HTMLElement>('.trace-host');
    const { start, end } = spanRef.current;
    if (!root || end <= start) return 0;
    const top = root.getBoundingClientRect().top + window.scrollY + start;
    return Math.min(1, Math.max(0, (y + window.innerHeight * 0.45 - top) / (end - start)));
  });
  const fill = useSpring(progress, { stiffness: 120, damping: 30, mass: 0.4 });

  useEffect(() => {
    const root = ref.current?.closest<HTMLElement>('.trace-host');
    if (!root) return;
    const steps = Array.from(root.querySelectorAll<HTMLElement>('[data-step]'));
    const setActive = (active: HTMLElement) => {
      let passed = true;
      for (const step of steps) {
        const point = step.querySelector<HTMLElement>('[data-testpoint]');
        if (!point) continue;
        point.dataset.state = step === active ? 'active' : passed ? 'passed' : 'idle';
        if (step === active) passed = false;
      }
    };
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting);
        if (visible.length === 0) return;
        const top = visible.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        setActive(top.target as HTMLElement);
      },
      { rootMargin: '-35% 0px -55% 0px' },
    );
    for (const step of steps) observer.observe(step);
    return () => observer.disconnect();
  }, []);

  const lineBox = {
    top: span.start,
    height: span.end - span.start,
    display: span.end > span.start ? undefined : 'none',
  };
  return (
    <div ref={ref} aria-hidden="true" className="notebook-trace">
      <div className="notebook-trace-line" style={lineBox} />
      <motion.div
        className="notebook-trace-fill"
        style={{ ...lineBox, ...(animate ? { scaleY: fill } : { scaleY: 1, opacity: 0.6 }) }}
      />
    </div>
  );
}
