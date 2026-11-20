// Contract tests for public_html/index.html: runs the inline script
// against a DOM stub and synthetic out/status payloads.
//
//   node tests/status-page.test.mjs
//
// Covered: log-format parsing (incl. the space-padded day that date(1) writes,
// and the legacy "(Python x.y)" suffix), run-state pill, stalled detection via
// Last-Modified, rate-limit handling, poll intervals.
import fs from 'node:fs';
import assert from 'node:assert/strict';

const HTML = fs.readFileSync(new URL('../public_html/index.html', import.meta.url), 'utf8');
const SCRIPT = HTML.match(/<script>([\s\S]*?)<\/script>/)[1];
const RULE = '-'.repeat(42);

function makeNode(tag = 'div') {
	const node = {
		tagName: tag,
		children: [],
		classes: [],
		dataset: {},
		attributes: {},
		textContent: '',
		title: '',
		scrollHeight: 0,
		scrollTop: 0,
		clientHeight: 0,
		set className(value) { this.classes = String(value).split(/\s+/).filter(Boolean); },
		get className() { return this.classes.join(' '); },
		classList: { add: (name) => { if (!node.classes.includes(name)) node.classes.push(name); } },
		setAttribute(key, value) { this.attributes[key] = value; },
		getAttribute(key) { return this.attributes[key]; },
		replaceChildren(...kids) {
			const flat = kids.flat();
			this.children = flat.flatMap((k) => (k.tagName === 'fragment' ? k.children : [k]));
			this.textContent = this.children.map((k) => k.textContent ?? '').join('');
		},
		append(...kids) { this.children.push(...kids); },
	};
	return node;
}

async function render({ log = '', status = 'false', lastModified = null, httpStatus = 200 }) {
	const ids = [...HTML.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
	const nodes = new Map(ids.map((id) => [id, makeNode()]));

	const documentStub = {
		hidden: false,
		getElementById: (id) => nodes.get(id),
		createElement: makeNode,
		createDocumentFragment: () => makeNode('fragment'),
		addEventListener() {},
	};

	const delays = [];
	const fetchStub = async (path) => {
		const isStatus = path.startsWith('status');
		if (httpStatus !== 200) return { ok: false, status: httpStatus, headers: { get: () => null } };
		return {
			ok: true,
			status: 200,
			headers: {
				get: (name) => (isStatus && name === 'last-modified' && lastModified
					? new Date(lastModified).toUTCString() : null),
			},
			text: async () => (isStatus ? `${status}\n` : log),
		};
	};

	new Function('document', 'fetch', 'setTimeout', 'setInterval', 'console', SCRIPT)(
		documentStub,
		fetchStub,
		(fn, delay) => { delays.push(delay); return delays.length; },
		() => 0,
		console,
	);
	await new Promise((resolve) => setTimeout(resolve, 40));

	const line = (id) => nodes.get(id).textContent;
	const classesOf = (name) => nodes.get('log').children
		.filter((c) => c.classes.includes(name)).map((c) => c.textContent);

	return {
		state: line('state'),
		pill: nodes.get('pill').dataset.state,
		started: line('stat-started'),
		finished: line('stat-finished'),
		threads: line('stat-threads'),
		updated: line('stat-updated'),
		errors: classesOf('is-error'),
		events: classesOf('is-event'),
		nextDelay: delays.at(-1),
	};
}

const logFor = (startLine, finishLine, extra = []) => [
	RULE, startLine, RULE, 'Python 3.13.18', 'Release version: 11.10.0',
	...extra, RULE, finishLine, RULE,
].join('\n');

const results = [];
function test(name, fn) {
	results.push([name, fn]);
}

// --- log format -----------------------------------------------------------

test('real run.sh format (no suffix) is parsed', async () => {
	const r = await render({
		log: logFor('Fri Nov 20 22:22:12 CET 2026 : Bot started',
			'Fri Nov 20 22:24:00 CET 2026 : Bot finished',
			['Archiving 4 thread(s).', 'Archiving 2 thread(s).']),
	});
	assert.equal(r.started, '20 Nov, 22:22');
	assert.equal(r.finished, '20 Nov, 22:24');
	assert.equal(r.threads, '6');
	assert.equal(r.pill, 'idle');
});

test('legacy "(Python 3.13.18)" suffix is still tolerated', async () => {
	const r = await render({
		log: logFor('Fri Nov 20 22:22:12 CET 2026 : Bot started (Python 3.13.18)',
			'Fri Nov 20 22:24:00 CET 2026 : Bot finished'),
	});
	assert.equal(r.started, '20 Nov, 22:22');
});

test('space-padded single-digit day (date(1) %e) compacts correctly', async () => {
	const r = await render({
		log: logFor('Fri Nov  6 05:00:01 CET 2026 : Bot started',
			'Fri Nov  6 05:03:10 CET 2026 : Bot finished'),
	});
	assert.equal(r.started, '6 Nov, 05:00', 'day 6 must not fall back to the raw string');
	assert.equal(r.finished, '6 Nov, 05:03');
});

test('simulated run markers are recognised', async () => {
	const r = await render({
		log: logFor('Fri Nov 20 22:22:12 CET 2026 : SIMULATED run started',
			'Fri Nov 20 22:24:00 CET 2026 : SIMULATED run finished (no edits saved)'),
	});
	assert.equal(r.started, '20 Nov, 22:22');
	assert.equal(r.finished, '20 Nov, 22:24');
	assert.equal(r.events.length >= 2, true);
});

test('errors and tracebacks are classified', async () => {
	const r = await render({
		log: logFor('Fri Nov 20 22:22:12 CET 2026 : Bot started',
			'Fri Nov 20 22:24:00 CET 2026 : Bot finished',
			['ERROR: Missing or malformed template in page [[no:X]]',
				'Traceback (most recent call last):']),
	});
	assert.equal(r.errors.length, 2);
});

// --- run state ------------------------------------------------------------

test('running run shows Archiving now and slow-ish polling', async () => {
	const r = await render({
		log: logFor('Fri Nov 20 22:22:12 CET 2026 : Bot started', null, ['Archiving 1 thread(s).']).replace(/\n-+\n$/, ''),
		status: 'true',
		lastModified: Date.now() - 60_000,
	});
	assert.equal(r.pill, 'running');
	assert.equal(r.state, 'Archiving now');
	assert.equal(r.finished, 'in progress');
	assert.equal(r.nextDelay, 5000, 'ramp must stay under Toolforge 100 req/min/IP');
});

test('idle run polls slowly', async () => {
	const r = await render({
		log: logFor('Fri Nov 20 22:22:12 CET 2026 : Bot started',
			'Fri Nov 20 22:24:00 CET 2026 : Bot finished'),
	});
	assert.equal(r.nextDelay, 15000);
});

test('status=true with a stale Last-Modified reports a stall', async () => {
	const r = await render({
		log: logFor('Fri Nov 20 18:00:00 CET 2026 : Bot started', null).replace(/\n-+\n$/, ''),
		status: 'true',
		lastModified: Date.now() - 4 * 60 * 60 * 1000,
	});
	assert.equal(r.pill, 'error');
	assert.equal(r.state, 'Stalled?');
	assert.equal(r.finished, 'stalled');
});

test('missing status file ("unknown") is treated as idle', async () => {
	const r = await render({ log: '', status: 'unknown' });
	assert.equal(r.pill, 'idle');
	assert.equal(r.started, 'no run in the log');
});

// --- transport ------------------------------------------------------------

test('HTTP 429 is reported as rate limiting with backoff', async () => {
	const r = await render({ httpStatus: 429 });
	assert.equal(r.pill, 'error');
	assert.equal(r.state, 'Rate limited');
	assert.ok(r.nextDelay >= 15000, `expected backoff, got ${r.nextDelay}`);
});

test('other HTTP failures are reported as unreachable', async () => {
	const r = await render({ httpStatus: 503 });
	assert.equal(r.state, 'Unreachable');
	assert.ok(r.errors.some((line) => line.includes('HTTP 503')));
});

// --- runner ---------------------------------------------------------------

let failed = 0;
for (const [name, fn] of results) {
	try {
		await fn();
		console.log(`PASS ${name}`);
	} catch (error) {
		failed += 1;
		console.log(`FAIL ${name}\n     ${error.message}`);
	}
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
