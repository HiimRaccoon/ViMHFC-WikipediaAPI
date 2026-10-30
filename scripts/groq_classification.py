"""Windows/local CPU-only Groq experiment; no corpus or pilot mutation."""
from pathlib import Path
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from vimfc.experiments.groq_classification.cli import main
if __name__=='__main__':
 try:raise SystemExit(main())
 except (Exception,KeyboardInterrupt) as error:
  # Exception values may include user/provider content: output only safe types.
  print(json.dumps({'status':'EXPERIMENT_STOPPED','error_type':type(error).__name__}),file=sys.stderr)
  raise SystemExit(3)
