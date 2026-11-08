"""Run fixed scanned jobs in a no-NIC guest, each with a five-second wall limit."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(sys.argv[1])
    jobs = json.loads((root / 'jobs.json').read_text())
    rows = []
    for job in jobs['jobs']:
        command = [sys.executable, '-I', '-B', '-c',
                   "import sys,runpy;sys.path.insert(0,'/mnt/fixtures');runpy.run_path('/mnt/fixtures/tavily_worker.py',run_name='__main__')",
                   str(root), str(root / job['spec'])]
        try:
            child = subprocess.run(command, capture_output=True, timeout=5,
                                   env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
            if child.returncode or len(child.stdout) > 32768:
                raise ValueError('WORKER_EXIT_OR_LIMIT')
            result = json.loads(child.stdout)
        except (subprocess.TimeoutExpired, ValueError):
            result = dict(outcome='REJECTED', reason='WORKER_FAILED')
        if result.get('outcome') not in job['expected']:
            raise RuntimeError('JOB_OUTCOME_MISMATCH:' + job['id'])
        rows.append(dict(id=job['id'], **result))
    output = dict(mode=jobs['mode'], results=rows,
                  network_interfaces=sorted(p.name for p in Path('/sys/class/net').iterdir()),
                  result_urls_followed=0)
    encoded = json.dumps(output, ensure_ascii=True).encode()
    if len(encoded) > 32768:
        raise RuntimeError('FRAME_LIMIT')
    print('TAVILY_PARSED_RESULT:' + encoded.decode(), flush=True)


if __name__ == '__main__':
    main()
