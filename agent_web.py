"""JSON entry point. --fixture needs only the Python standard library."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / 'poc/applications/public_web_agent_v01/agent_reader.py'), run_name='__main__')
