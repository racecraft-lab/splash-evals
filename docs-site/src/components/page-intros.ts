type Step = [string, string];
type Intro = {
  summary: string;
  variant?: 'results' | 'comparison';
  caption: string;
  steps?: Step[];
};

/** Reader-facing context, not additional evaluation evidence. */
export const pageIntros: Record<string, Intro> = {
  '': {
    summary: 'How close can local AI come to the frontier? We measure practical ability, compare like-for-like evidence, and show where the gaps remain.',
    caption: 'From an instruction to inspectable evidence.',
    steps: [['Task', 'Give the model clear instructions'], ['Local model', 'Run it through LM Studio'], ['Answer check', 'Record what passed—and what it means']],
  },
  dashboard: {
    summary: 'Compare the research targets, inspect the available evidence, and see what still needs measuring. No frontier gap has been established yet.',
    caption: 'The research question is open. Missing scores are not zero.',
    steps: [['Local capability', 'Not yet measured on eligible tasks'], ['Frontier gap', 'Not yet measured under matching conditions'], ['Next milestone', 'A frozen, qualified capability study']],
  },
  'local-pilot-results': {
    summary: 'A closer look at the run: what completed, how answers were checked, and why this is a setup check rather than a verdict on ability.',
    variant: 'results', caption: 'Reused setup-check cases. Not a capability score.',
  },
  methodology: {
    summary: 'A good test needs more than a plausible answer. Follow the path from a clear question to a result you can interpret.',
    caption: 'The method separates the task, the check, and the claim.',
    steps: [['Define', 'Choose the task and expected checks'], ['Run', 'Record what the local model does'], ['Interpret', 'Explain the result and its limits']],
  },
  'benchmark-tasks': {
    summary: 'What should a useful local model be able to do? Explore the task families planned for testing—not a list of completed evaluations.',
    caption: 'Planned coverage is not measured performance.',
    steps: [['Practical work', 'Tasks with useful, checkable outcomes'], ['Reasoning', 'Problems that require several steps'], ['Reliability', 'Checks for consistent behavior']],
  },
  architecture: {
    summary: 'The model runs locally. The public website shows reviewed summaries. These are separate systems with a deliberate boundary.',
    caption: 'Public CI has no connection to the local model runtime.',
    steps: [['Local execution', 'LM Studio runs the model'], ['Private evidence', 'Detailed run records stay outside Git'], ['Public summary', 'Only reviewed material is published']],
  },
  privacy: {
    summary: 'Useful evidence does not require publishing everything. See what readers can inspect and what stays private.',
    caption: 'Review comes before publication.',
    steps: [['Keep private', 'Raw prompts, responses, and runtime records'], ['Review', 'Check identity, permissions, and content'], ['Publish', 'Approved summaries and methods']],
  },
  operations: {
    summary: 'Prepare a local workspace, check the runtime, and run the evaluation workflow with private state kept out of Git.',
    caption: 'Work through the prerequisites before running commands.',
    steps: [['Prepare', 'Set up the workspace and private state'], ['Connect locally', 'Check LM Studio and the chosen model'], ['Run and review', 'Inspect evidence before sharing anything']],
  },
  'capability-readiness': {
    summary: 'The 60-case study is frozen and technically ready. See which safeguards passed, what the operator must approve, and why no capability result exists yet.',
    caption: 'Preparation is complete. Approval, execution, and publication remain separate gates.',
    steps: [['Prepared', 'Frozen tasks, scorers, budget, and local runtime'], ['Awaiting approval', 'Human confirmation that held-out cases stayed unseen'], ['After the run', 'Review and publish only safe aggregate evidence']],
  },
  sources: {
    summary: 'Older leading-model reports provide context. Their dates, task versions, and scoring conditions determine how useful that context is.',
    caption: 'A reported number needs its original conditions.',
    steps: [['Original source', 'Find the paper or model report'], ['Dated record', 'Keep the task and scoring details'], ['Comparison check', 'Ask whether the conditions match']],
  },
  'historical-frontier-comparison': {
    summary: 'The goal is to measure the gap to current and previous-generation frontier models. Here is the study plan, the available context, and what still blocks a fair comparison.',
    variant: 'comparison', caption: 'Matching tasks and scoring conditions are required before comparing scores.',
  },
  'frontier-catalog': {
    summary: 'Read the historical evidence as a catalog of sourced records, not a leaderboard or a count of unique models.',
    caption: 'Follow each record back to its source and conditions.',
    steps: [['Source', 'Where the number was reported'], ['Conditions', 'What was tested and how it was scored'], ['Limits', 'What the record cannot establish']],
  },
  'frontier-verification': {
    summary: 'Checking a copied number is one job. Checking whether two tests are comparable is another. This page addresses the first.',
    caption: 'A correct transcription does not establish comparability.',
    steps: [['Find', 'Locate the original reported value'], ['Check', 'Compare it with the catalog record'], ['Separate', 'Assess comparison eligibility on its own']],
  },
  glossary: {
    summary: 'You should not need an AI research background to read the results. Start with the terms that change how a claim should be understood.',
    caption: 'Three distinctions that matter when reading results.',
    steps: [['Setup check', 'Can the testing path operate?'], ['Capability test', 'How well does the model handle the tasks?'], ['Comparison', 'Were models tested under matching conditions?']],
  },
};
