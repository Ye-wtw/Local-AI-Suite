/**
 * model-arena — expose the local Model Arena evaluation system as DSH tools.
 *
 * WHY THIS PLUGIN IS THIN ON PURPOSE
 *
 * The product goal is a measurement that is as free of interference as possible,
 * "including interference from the project itself". This plugin runs inside the DSH
 * process, so it must never do the measuring: it would compete for CPU with the
 * `--cpu-moe` server it is trying to measure, and the tok/s numbers would be wrong
 * in a way nobody could see.
 *
 * So the split is:
 *   - arena_core (a separate process)  does the evaluation
 *   - this plugin                      reads its results and asks it to run things
 *
 * Everything here is a thin client: read JSON from the results directory, or spawn
 * the Python CLI and stream back its output. No judging, no scoring, no timing.
 *
 * Tools:
 *   arena_status     what has been evaluated, and how trustworthy each run is
 *   arena_compare    paired comparison of two models (or two launch profiles)
 *   arena_launch     the launch parameters a run actually used
 *   arena_run        start an evaluation (long; reports the exact command)
 *   arena_sweep      show the parameter-sweep plan and cost
 */

export const name = 'model-arena'

/** Only the subprocess service is needed; this plugin registers tools and runs CLIs. */
export const inject = ['subprocess', 'tools']

const NL = '\n'

/** Resolve the arena project root. Config wins; otherwise use the known workspace path. */
function projectRoot(config) {
  const fromConfig = config?.projectRoot
  if (typeof fromConfig === 'string' && fromConfig.length > 0) return fromConfig
  return 'E:\\AI_workspace\\06_model-arena'
}

/** Run a python CLI inside the project and return {stdout, stderr, code}. */
async function runPython(ctx, root, args, signal, timeoutMs) {
  const py = (await ctx.subprocess.resolveExecutable('python', undefined, signal)) ?? 'python'
  const handle = ctx.subprocess.spawn({
    argv: [py, ...args],
    cwd: root,
    stdio: {
      stdin: 'ignore',
      stdout: { maxBytes: 4 * 1024 * 1024 },
      stderr: { maxBytes: 512 * 1024 },
    },
    ...(signal !== undefined ? { signal } : {}),
    graceMs: 5000,
  })
  const result = await handle.wait({ timeoutMs: timeoutMs ?? 120000 })
  const out = await handle.stdout?.text?.() ?? ''
  const err = await handle.stderr?.text?.() ?? ''
  return { stdout: String(out), stderr: String(err), code: result?.exitCode ?? null }
}

/** Read a file through python (avoids depending on node fs permissions here). */
async function readJson(ctx, root, relPath, signal) {
  const r = await runPython(ctx, root, ['-c',
    'import sys,io;sys.stdout.reconfigure(encoding="utf-8");' +
    'print(open(sys.argv[1],encoding="utf-8").read())', relPath], signal, 30000)
  if (r.code !== 0) throw new Error(r.stderr || 'read failed')
  return JSON.parse(r.stdout)
}

const textOut = (schemaProps) => ({
  schema: { type: 'object', additionalProperties: true, properties: schemaProps ?? {} },
  render: (_args, value) => [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value, null, 1) }],
})

export function apply(ctx, config) {
  const root = projectRoot(config)

  // ---------------------------------------------------------------- status
  ctx.tools.register({
    name: 'arena_status',
    description: [
      'Show what the local Model Arena has evaluated: models, scores, coverage,',
      'and the environment trust level of each run.',
      '',
      'Use when the user asks about model comparison, which local model is best,',
      'or past evaluation results. Read-only; runs no model.',
    ].join(NL),
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: textOut({ text: { type: 'string' } }),
    async execute(_args, exec) {
      const r = await runPython(ctx, root, ['runner/arena_cli.py', 'status'], exec?.signal, 60000)
      return { text: (r.stdout || r.stderr || '(no output)').trim() }
    },
  })

  // ---------------------------------------------------------------- compare
  ctx.tools.register({
    name: 'arena_compare',
    description: [
      'Paired comparison of two models on the SAME questions, with a significance',
      'test. Pairing matters: comparing two independent accuracy numbers overstates',
      'confidence, because both runs answered identical items.',
      '',
      'Returns per-dimension deltas and a win/loss/tie count.',
    ].join(NL),
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        a: { type: 'string', description: 'baseline model name' },
        b: { type: 'string', description: 'model to compare against the baseline' },
        level: { type: 'string', description: 'test level, e.g. canary (default) or smoke' },
      },
      required: ['a', 'b'],
    },
    output: textOut({ text: { type: 'string' } }),
    async execute(args, exec) {
      const argv = ['runner/arena_cli.py', 'compare', '--a', String(args.a), '--b', String(args.b)]
      if (args.level) argv.push('--level', String(args.level))
      const r = await runPython(ctx, root, argv, exec?.signal, 90000)
      return { text: (r.stdout || r.stderr || '(no output)').trim() }
    },
  })

  // ---------------------------------------------------------------- launch params
  ctx.tools.register({
    name: 'arena_launch',
    description: [
      'Show the FULL launch command line a local model server was started with',
      '(ctx-size, n-gpu-layers, KV cache type, MTP, Flash Attention...), plus the',
      'derived values and any saved launch profiles.',
      '',
      'A score without these parameters is not reproducible, so this is the tool to',
      'reach for when the user asks "what config was that measured with".',
    ].join(NL),
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        profile: { type: 'string', description: 'optional: show one saved profile by name' },
      },
    },
    output: textOut({ text: { type: 'string' } }),
    async execute(args, exec) {
      const argv = ['runner/arena_cli.py', 'launch']
      if (args.profile) argv.push('--profile', String(args.profile))
      const r = await runPython(ctx, root, argv, exec?.signal, 60000)
      return { text: (r.stdout || r.stderr || '(no output)').trim() }
    },
  })

  // ---------------------------------------------------------------- sweep plan
  ctx.tools.register({
    name: 'arena_sweep',
    description: [
      'Show the parameter-sweep plan: which launch flags can affect quality vs only',
      'speed, how many combinations survive the physical constraints, and the',
      'estimated wall-clock for the funnel.',
      '',
      'Planning only — it runs nothing. Use before proposing an optimisation run.',
    ].join(NL),
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        stage: { type: 'string', description: '0 = constraints only (default)' },
      },
    },
    output: textOut({ text: { type: 'string' } }),
    async execute(args, exec) {
      const argv = ['runner/arena_cli.py', 'sweep']
      if (args.stage) argv.push('--stage', String(args.stage))
      const r = await runPython(ctx, root, argv, exec?.signal, 60000)
      return { text: (r.stdout || r.stderr || '(no output)').trim() }
    },
  })

  // ---------------------------------------------------------------- run (long)
  ctx.tools.register({
    name: 'arena_run',
    description: [
      'Start an evaluation run on a local model endpoint.',
      '',
      'WARNING: this is slow (a canary level is ~25 min, agent mode is far slower)',
      'and it occupies the GPU. It returns the exact command it would run and, when',
      'confirm is true, executes it and waits.',
      '',
      'Prefer arena_status first to see whether a run already exists.',
    ].join(NL),
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        suite: { type: 'string', description: 'smoke | coarse | canary | full | agent' },
        endpoint: { type: 'string', description: 'endpoint profile, default local' },
        agentMode: {
          type: 'string',
          description: 'bare | agent-small | agent-mid | agent-real (tool environment injected into the prompt)',
        },
        confirm: { type: 'boolean', description: 'must be true to actually execute' },
      },
      required: ['suite'],
    },
    output: textOut({ text: { type: 'string' } }),
    async execute(args, exec) {
      const argv = ['runner/run.py', '--suite', String(args.suite),
        '--endpoint', String(args.endpoint ?? 'local')]
      if (args.agentMode) argv.push('--agent-mode', String(args.agentMode))
      const cmdline = 'python ' + argv.join(' ')
      if (args.confirm !== true) {
        return { text: [
          '未执行 —— 这是评测命令,会占用 GPU 且耗时较长。',
          '',
          '命令:',
          '  ' + cmdline,
          '',
          '确认后带 confirm=true 再调用。',
        ].join(NL) }
      }
      const r = await runPython(ctx, root, argv, exec?.signal, 6 * 3600 * 1000)
      const tail = (r.stdout || '').split(NL).slice(-25).join(NL)
      return { text: ['命令: ' + cmdline, '', '退出码: ' + r.code, '', tail,
        r.stderr ? NL + 'stderr:' + NL + r.stderr.slice(-800) : ''].join(NL) }
    },
  })
}
