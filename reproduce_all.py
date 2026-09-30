"""Run the included reproduction checks and compare the recreated results."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    manifest = json.loads((ROOT / 'PACKAGE_MANIFEST.json').read_text())
    for relative, expected in manifest['files'].items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            raise SystemExit('File missing or changed: ' + relative)
    print('Package files match the prepared copy.', flush=True)
    if '--check' in sys.argv[1:]:
        return
    out = ROOT / 'verification'
    out.mkdir(exist_ok=True)
    records = []
    for number, args in enumerate(manifest['commands'], 1):
        print(f'[{number}/12] python3 -B ' + ' '.join(args), flush=True)
        result = subprocess.run([sys.executable, '-B', *args], cwd=ROOT,
                                capture_output=True, text=True, timeout=600)
        log = out / f'command_{number:02d}.log'
        log.write_text(result.stdout + result.stderr)
        records.append({'command': args, 'returncode': result.returncode,
                        'log': str(log.relative_to(ROOT))})
        (out / 'ALL_COMMANDS.json').write_text(json.dumps(records, indent=2) + '\n')
        if result.returncode:
            print(result.stdout + result.stderr)
            raise SystemExit(result.returncode)
        # Save the printed evidence-growth result for comparison with the expected output.
        if args == ['catalogue_r3/example_evidence_growth.py']:
            p = ROOT / 'catalogue_r3/EVIDENCE_GROWTH.json'
            p.write_text(result.stdout)
    for relative, expected in manifest['expected_generated_files'].items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            raise SystemExit('Generated result differs from reviewed output: ' + relative)
    print('PASS: 12 commands passed; regenerated plans and evidence-growth output match.')
    print('See verification/ and job_example/results/. No simulation was run.')

if __name__ == '__main__':
    main()
