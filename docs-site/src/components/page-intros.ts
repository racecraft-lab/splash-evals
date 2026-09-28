import records from '../data/benchmark-records.json';
import { benchmarkLifecycleView } from '../../scripts/generate-content.mjs';
import { codingIntro } from '../../scripts/benchmark-detail.mjs';

export const codingView = benchmarkLifecycleView(records.benchmarks['SWE-bench Verified'].result, {
  benchmarkName: 'SWE-bench Verified', requiredCompleteCount: 500, errorsCountAsUnresolved: true,
});

type Step = [string, string];
type IntroBase = {
  summary: string;
  caption: string;
  /** Small red label above the title. */
  kicker?: string;
  /** Phrase of the title shown in the blue accent. */
  accent?: string;
  /** Overview only: links to the page's test points, as [label, href]. */
  journey?: Step[];
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
        /** Each measured result as [score, plain label]. */
        results: Step[];
      };
    }
);

/** Reader-facing context, not additional evaluation evidence. */
export const pageIntros: Record<string, Intro> = {
  '': {
    kicker: 'Experiment 01 · Local helper check',
    accent: 'local AI helper',
    summary: 'Paid AI coding plans have usage limits. When you hit them, work stops. We are testing a simple idea: let a free AI model on your own computer do the routine reading and coding, so the paid model only handles the hard parts.',
    variant: 'overview',
    caption: 'Step one of a larger study: is a local model good enough to take work off a paid plan?',
    study: {
      model: 'Qwen3.8-27B via Splash',
      foundation: 'On a Mac with 128 GB of memory · nothing sent to the cloud',
      results: [
        [codingView.label, 'Real coding fixes · SWE-bench Verified'],
        ['54.55%', 'Expert science questions · GPQA Diamond'],
      ],
    },
    journey: [
      ['The problem', '#the-problem'],
      ['The test', '#the-test'],
      ['What we found', '#what-we-found'],
      ['What it means', '#what-it-means'],
    ],
  },
  dashboard: {
    kicker: 'The evidence',
    summary: 'We gave the model two standard tests. Each one has its own page with the score, how the test worked, and what the score does and does not tell you.',
    caption: 'Two tests, two separate results. They are never added together.',
    steps: [['Science questions', 'GPQA Diamond · measured'], ['Coding fixes', `SWE-bench Verified · ${codingView.detail}`], ['Read carefully', 'Each test measures something different']],
  },
  'dashboard/gpqa-diamond': {
    kicker: 'Hard science questions',
    summary: 'We gave the model 198 hard science questions written by experts. It answered 54.55% of them correctly, just over half, and every question finished without an error.',
    variant: 'results',
    caption: 'Our measured result, shown next to published cloud-model scores for context. Those scores come from different setups, so this is not a head-to-head race.',
    result: {
      score: '54.55%',
      metric: 'GPQA Diamond accuracy',
      completion: '198 of 198 completed · 0 errors',
    },
  },
  'dashboard/swe-bench-verified': { kicker: 'Real coding fixes', ...codingIntro(codingView) } as Intro,
  'what-it-means': {
    kicker: 'The conclusion',
    summary: 'The local helper did well enough to try on everyday reading and coding. It does not replace the best cloud models on the hardest problems. We have not yet measured how much it saves.',
    caption: 'What the two results support, what they do not, and what we test next.',
    steps: [['Short answer', 'Promising enough to try as a helper'], ['Not shown yet', 'How much it saves on a real plan'], ['Next', 'Measure savings with a delegation plugin']],
  },
  methodology: {
    kicker: 'The method',
    summary: 'A fair test needs more than a good answer. Here is what we measured, how we measured it, and why we do not rank our result against cloud models.',
    caption: 'The method keeps the task, the check, and the claim separate.',
    steps: [['Define', 'Pick the test and how answers are checked'], ['Run', 'Record exactly what the local model does'], ['Interpret', 'Explain the result and its limits']],
  },
  operations: {
    kicker: 'For builders',
    summary: 'For developers who want to reproduce the tests. Set up a local workspace, check the model runtime, and run the same evaluation, with private data kept out of Git.',
    caption: 'Work through the setup before running any commands.',
    steps: [['Prepare', 'Set up the workspace and private state'], ['Connect locally', 'Check LM Studio and the chosen model'], ['Run and review', 'Inspect evidence before sharing anything']],
  },
  sources: {
    kicker: 'Where the numbers come from',
    summary: 'Every score on this site has a source. See who measured each one, where it was published, and how its test differed from ours.',
    variant: 'sources',
    caption: 'The score, its source, and its test conditions always stay together.',
    sourceRecord: {
      score: '54.55%',
      benchmark: 'GPQA Diamond',
      measuredBy: 'Racecraft local run',
      status: 'Reviewed local result',
    },
  },
};
