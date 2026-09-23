import records from '../data/benchmark-records.json';
import { benchmarkLifecycleView } from '../../scripts/generate-content.mjs';
import { codingIntro } from '../../scripts/benchmark-detail.mjs';

export const codingView = benchmarkLifecycleView(records.benchmarks['SWE-bench Verified'].result, {
  benchmarkName: 'SWE-bench Verified', requiredCompleteCount: 500,
});

type Step = [string, string];
type IntroBase = {
  summary: string;
  caption: string;
};
type Intro = IntroBase & (
  | {
      variant?: undefined;
      steps: Step[];
    }
  | {
      variant: 'results';
      result: {
        score: string;
        metric: string;
        completion: string;
      };
    }
  | {
      variant: 'sources';
      sourceRecord: {
        score: string;
        benchmark: string;
        measuredBy: string;
        status: string;
      };
    }
  | {
      variant: 'overview';
      study: {
        model: string;
        foundation: string;
        score: string;
        benchmark: string;
        referenceCount: string;
        referenceLabel: string;
      };
    }
);

/** Reader-facing context, not additional evaluation evidence. */
export const pageIntros: Record<string, Intro> = {
  '': {
    summary: 'Splash is Inco AI’s local inference engine. Here it runs the Qwen Team’s Qwen3.8-27B model through LM Studio on a Mac, while Racecraft Lab independently measures the result.',
    variant: 'overview',
    caption: 'Local benchmark evidence, with separate pages for science reasoning and repository repair.',
    study: {
      model: 'Splash + Qwen3.8-27B',
      foundation: 'Inco AI runtime · Qwen Team model · LM Studio',
      score: '54.55%',
      benchmark: 'GPQA Diamond accuracy',
      referenceCount: '20',
      referenceLabel: 'published frontier observations · directional',
    },
  },
  dashboard: {
    summary: 'Choose a benchmark to see its reviewed evidence, external context, method, runtime, and limits. Each benchmark has its own result and testing conditions.',
    caption: 'Two benchmarks, two evidence states, no blended score.',
    steps: [['Measured', 'GPQA Diamond · reviewed local result'], [codingView.label, `SWE-bench Verified · ${codingView.detail}`], ['Compare carefully', 'Keep every metric and protocol separate']],
  },
  'dashboard/gpqa-diamond': {
    summary: 'Splash scored 54.55% accuracy on the full 198-question GPQA Diamond benchmark. All 198 questions completed with no execution errors.',
    variant: 'results',
    caption: 'A measured local result alongside source-labeled frontier context, not a protocol-matched head-to-head.',
    result: {
      score: '54.55%',
      metric: 'GPQA Diamond accuracy',
      completion: '198 of 198 completed · 0 errors',
    },
  },
  'dashboard/swe-bench-verified': codingIntro(codingView) as Intro,
  methodology: {
    summary: 'A good test needs more than a plausible answer. Follow the path from a clear question to a result you can interpret.',
    caption: 'The method separates the task, the check, and the claim.',
    steps: [['Define', 'Choose the task and expected checks'], ['Run', 'Record what the local model does'], ['Interpret', 'Explain the result and its limits']],
  },
  operations: {
    summary: 'Prepare a local workspace, check the runtime, and run the evaluation workflow with private state kept out of Git.',
    caption: 'Work through the prerequisites before running commands.',
    steps: [['Prepare', 'Set up the workspace and private state'], ['Connect locally', 'Check LM Studio and the chosen model'], ['Run and review', 'Inspect evidence before sharing anything']],
  },
  sources: {
    summary: 'See where each score came from, who measured it, and which test details matter before you compare models.',
    variant: 'sources',
    caption: 'The score, source, and test condition stay together.',
    sourceRecord: {
      score: '54.55%',
      benchmark: 'GPQA Diamond',
      measuredBy: 'Racecraft local run',
      status: 'Reviewed local result',
    },
  },
};
