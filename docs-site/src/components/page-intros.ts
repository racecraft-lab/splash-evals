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
);

/** Reader-facing context, not additional evaluation evidence. */
export const pageIntros: Record<string, Intro> = {
  '': {
    summary: 'How close can local AI come to the frontier? We measure practical ability, compare like-for-like evidence, and show where the gaps remain.',
    caption: 'From an instruction to inspectable evidence.',
    steps: [['Task', 'Give the model clear instructions'], ['Local model', 'Run it through LM Studio'], ['Answer check', 'Record what passed—and what it means']],
  },
  dashboard: {
    summary: 'Splash scored 54.55% accuracy on the full 198-question GPQA Diamond benchmark. All 198 questions completed with no execution errors.',
    variant: 'results',
    caption: 'A measured local result. Frontier scores are publisher-reported context, not a protocol-matched head-to-head.',
    result: {
      score: '54.55%',
      metric: 'GPQA Diamond accuracy',
      completion: '198 of 198 completed · 0 errors',
    },
  },
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
    summary: 'Trace the local result and frontier context to their original records. Dates, task versions, and scoring conditions determine what can be compared.',
    caption: 'A reported number needs its original conditions.',
    steps: [['Original source', 'Find the paper or model report'], ['Dated record', 'Keep the task and scoring details'], ['Comparison check', 'Ask whether the conditions match']],
  },
};
