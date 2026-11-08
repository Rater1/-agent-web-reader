"""Bounded VirtualBox state-query retry; no acquisition entry point."""
import importlib.util
from pathlib import Path
import time
BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('public_previous_pipeline', BASE/'tavily_pipeline_v01/run_pipeline.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
class OfficialPipeline(previous.Pipeline):
    def vbox(self, *args):
        for attempt in range(6):
            try:
                return super().vbox(*args)
            except RuntimeError as exc:
                if not args or args[0] != 'showvminfo' or exc.args != ('CHILD_COMMAND_FAILED',):
                    raise
                try:
                    with (self.evidence / 'failed_child_stderr.txt').open('rb') as stream:
                        detail = stream.read(8193)
                except OSError:
                    raise exc
                if len(detail) > 8192 or not all((marker in detail for marker in [b'VBOX_E_INVALID_OBJECT_STATE', b'Failed to get a console object'])):
                    raise
                self.record['vm_info_transient_errors'] = self.record.get('vm_info_transient_errors', 0) + 1
                if attempt == 5:
                    raise RuntimeError('VM_INFO_RETRY_EXHAUSTED') from None
                time.sleep(0.5)
