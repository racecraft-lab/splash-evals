// This is a presentation of the existing reviewed public aggregate, not a raw-data exporter.
export function qualificationContent(record) {
  if (record.publication_purpose !== 'post_hoc_runtime_scorer_qualification' ||
      record.capability_evidence !== false || record.historical_comparison !== 'prohibited') {
    throw new Error('The setup-check presentation cannot render capability evidence.');
  }
  const counts = ['planned', 'attempted', 'completed', 'scorable', 'accepted', 'failed', 'censored', 'unattempted'];
  if (counts.some((key) => !Number.isInteger(record[key]) || record[key] < 0) ||
      record.planned !== record.attempted + record.unattempted ||
      record.attempted !== record.completed + record.failed + record.censored ||
      record.scorable > record.completed || record.accepted > record.scorable ||
      record.planned === 0 || record.accepted !== record.planned) {
    throw new Error('Missing or inconsistent qualification counts.');
  }
  if (!Array.isArray(record.wilson_95) || record.wilson_95.length !== 2 ||
      record.wilson_95.some((value) => typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) ||
      record.wilson_95[0] > record.wilson_95[1]) {
    throw new Error('Missing or invalid qualification interval.');
  }
  const finding = `<section class="finding" aria-label="Current finding">
<h2>First check: the testing setup worked</h2>
<p class="result-count">${record.accepted} of ${record.planned} reused cases completed and passed their checks.</p>
<p>Broader ability still needs a fresh test set. Direct comparisons with older leading models are not yet available.</p>
</section>`;
  const labels = ['Planned', 'Attempted', 'Completed', 'Could be scored', 'Accepted by the checker', 'Failed', 'Stopped or censored', 'Not attempted'];
  const table = '| Outcome | Cases |\n| --- | ---: |\n' + counts.map((key, index) => `| ${labels[index]} | ${record[key]} |`).join('\n');
  const interval = record.wilson_95.map((value) => `${(value * 100).toFixed(1)}%`).join('–');
  const technical = `- Publication purpose: \`post_hoc_runtime_scorer_qualification\`\n- Capability evidence: \`false\`\n- Historical comparison: prohibited\n- Wilson 95% interval: ${interval} (qualification aggregate only)\n\n[Exact reviewed values and provenance](https://github.com/racecraft-lab/splash-evals/blob/main/docs/qualification-summary.json)`;
  return { finding, table, technical };
}
