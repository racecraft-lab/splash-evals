// One request per process. No model loading, tools, redirects, or fallback.
import { pathToFileURL } from 'node:url';
import { LMStudioClient } from '@lmstudio/sdk';

const MAX_OUTPUT = 65536;
const MODEL_KEY = 'qwen3.8-27b-splash';
const CONTEXT = 131072;
const MAX_BYTES = 8 * 1024 * 1024;

export function validateRequest(request) {
  const body = request?.body;
  if (request?.endpoint !== 'ws://127.0.0.1:1234' ||
      typeof body?.model !== 'string' || !/^[a-zA-Z0-9._-]{1,128}$/.test(body.model) ||
      body.temperature !== 1 || body.top_p !== 0.95 ||
      body.max_tokens !== MAX_OUTPUT || body.reasoning_effort !== 'on' ||
      body.stream !== false || !Array.isArray(body.messages) || !body.messages.length ||
      body.messages.some(m => !m || !['system', 'user', 'assistant'].includes(m.role) ||
        typeof m.content !== 'string' || Object.keys(m).some(k => !['role', 'content'].includes(k))) ||
      Object.keys(body).some(k => !['model', 'messages', 'temperature', 'top_p', 'max_tokens', 'reasoning_effort', 'stream'].includes(k))) {
    throw new Error('request_contract_mismatch');
  }
  return body;
}

function validateIdentity(info, alias) {
  if (info?.modelKey !== MODEL_KEY || info.identifier !== alias ||
      info.deviceIdentifier !== null || info.format !== 'yuzu') {
    throw new Error('local_model_identity_mismatch');
  }
}

export function validateBinding(before, after, alias) {
  validateIdentity(before, alias);
  validateIdentity(after, alias);
  if (typeof before.instanceReference !== 'string' || !before.instanceReference ||
      before.instanceReference !== after.instanceReference ||
      before.contextLength !== CONTEXT || after.contextLength !== CONTEXT) {
    throw new Error('loaded_instance_changed');
  }
}

function fields(config) {
  if (!Array.isArray(config?.fields)) throw new Error('missing_applied_config');
  const entries = config.fields.map(f => [f.key, f.value]);
  if (new Set(entries.map(([key]) => key)).size !== entries.length) throw new Error('duplicate_config_field');
  return Object.fromEntries(entries);
}

export function verifiedResponse(result, alias, expectedInstanceReference, cancelled = false) {
  validateIdentity(result.modelInfo, alias);
  if (typeof expectedInstanceReference !== 'string' || !expectedInstanceReference ||
      result.modelInfo.instanceReference !== expectedInstanceReference) {
    throw new Error('response_instance_changed');
  }
  const config = fields(result.predictionConfig);
  const load = fields(result.loadConfig);
  const topP = config['llm.prediction.topPSampling'];
  const maximum = config['llm.prediction.maxPredictedTokens'];
  if (config['llm.prediction.temperature'] !== 1 || topP?.checked !== true ||
      topP.value !== 0.95 || maximum?.checked !== true || maximum.value !== MAX_OUTPUT ||
      config['llm.prediction.reasoning.enableThinking'] !== true ||
      load['llm.load.contextLength'] !== CONTEXT) throw new Error('applied_settings_mismatch');
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const output = count(result.stats?.predictedTokensCount);
  const input = count(result.stats?.promptTokensCount);
  if (output !== null && output > MAX_OUTPUT) throw new Error('output_token_limit_exceeded');
  const stopReason = result.stats?.stopReason;
  const stopped = cancelled || stopReason === 'userStopped';
  if (!stopped && !['eosFound', 'maxPredictedTokensReached', 'contextLengthReached'].includes(stopReason)) {
    throw new Error('unrecognized_completion_status');
  }
  if (typeof result.content !== 'string' || Buffer.byteLength(result.content) > MAX_BYTES) {
    throw new Error('response_content_invalid');
  }
  return {
    model: alias,
    status: stopped ? 'cancelled' : 'completed',
    choices: stopped ? [] : [{ index: 0, message: { role: 'assistant', content: result.content },
      finish_reason: stopReason === 'eosFound' ? 'stop' : 'length' }],
    usage: { completion_tokens: output, prompt_tokens: input,
      total_tokens: output === null || input === null ? null : output + input },
    charged_output_tokens: output ?? MAX_OUTPUT,
    evidence: { model_key: MODEL_KEY, device_identifier: null, format: 'yuzu',
      temperature: 1, top_p: 0.95, max_output_tokens: MAX_OUTPUT, reasoning: 'on',
      context_length: CONTEXT, stop_reason: stopReason ?? null, transport: 'lmstudio-sdk-2.0.0' },
  };
}

async function readInput() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_BYTES) throw new Error('request_bytes_exceeded');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function main() {
  const abort = new AbortController();
  process.on('SIGTERM', () => abort.abort());
  let phase = 'read_request';
  try {
    const request = await readInput();
    const body = validateRequest(request);
    // SDK diagnostic messages can contain raw model text. Do not forward them.
    const logger = Object.fromEntries(['info', 'warn', 'error', 'debug'].map(k => [k, () => {}]));
    const client = new LMStudioClient({ baseUrl: request.endpoint, logger });
    const model = client.llm.createDynamicHandle({ identifier: body.model, deviceIdentifier: null });
    phase = 'before_identity';
    const before = await model.getModelInfo();
    validateBinding(before, before, body.model);
    phase = 'prediction';
    const result = await model.respond(body.messages, { enableThinking: true, temperature: 1,
      topPSampling: 0.95, maxTokens: MAX_OUTPUT, signal: abort.signal });
    phase = 'applied_settings';
    const response = verifiedResponse(result, body.model, before.instanceReference, abort.signal.aborted);
    phase = 'after_identity';
    const after = await model.getModelInfo();
    validateBinding(before, after, body.model);
    process.stdout.write(JSON.stringify(response), () => process.exit(0));
  } catch {
    process.stdout.write(JSON.stringify({ status: 'failed', choices: [], usage: {
      completion_tokens: null, prompt_tokens: null, total_tokens: null },
      charged_output_tokens: MAX_OUTPUT, error: 'sdk_transport_refused', phase }), () => process.exit(1));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
