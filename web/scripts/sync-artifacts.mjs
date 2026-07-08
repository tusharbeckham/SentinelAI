#!/usr/bin/env node
/**
 * Copy the measured pipeline artifacts into the front end's public/data folder.
 *
 * The console renders real numbers or it renders nothing - there is no mock data
 * anywhere in this app, which is why this script fails loudly instead of writing
 * placeholders when the pipeline has not been run yet.
 *
 *   node scripts/sync-artifacts.mjs            # ../artifacts -> public/data
 *   node scripts/sync-artifacts.mjs ../out     # custom artifacts dir
 */
import { copyFile, mkdir, stat } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const webRoot = resolve(here, '..')
const source = resolve(webRoot, process.argv[2] ?? '../artifacts')
const target = join(webRoot, 'public', 'data')

// Only what the UI actually reads. scored_test_windows.csv is 4.5 MB and is
// deliberately NOT shipped to the browser.
const REQUIRED = ['report.json', 'alerts.json', 'soar_decisions.json']
const OPTIONAL = ['budget_sweep.json', 'drift_psi.json', 'audit_log.json']

async function exists(path) {
	try {
		await stat(path)
		return true
	} catch {
		return false
	}
}

async function main() {
	if (!(await exists(source))) {
		console.error(
			`No artifacts at ${source}\n` +
				'Run the pipeline first:\n' +
				'  python -m sentinelai.pipeline --out artifacts --budget 50',
		)
		process.exit(1)
	}
	await mkdir(target, { recursive: true })

	let copied = 0
	for (const name of REQUIRED) {
		const from = join(source, name)
		if (!(await exists(from))) {
			console.error(`Missing required artifact ${name} in ${source}.`)
			process.exit(1)
		}
		await copyFile(from, join(target, name))
		copied += 1
	}
	for (const name of OPTIONAL) {
		const from = join(source, name)
		if (await exists(from)) {
			await copyFile(from, join(target, name))
			copied += 1
		}
	}
	console.log(`synced ${copied} artifact files -> public/data`)
}

main().catch((err) => {
	console.error(err)
	process.exit(1)
})
