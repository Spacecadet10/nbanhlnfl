import sys, os, json, time, io, contextlib
sys.path.insert(0, os.path.dirname(__file__))
import harness
data_dir, plan_path, start_at, out_path = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
t, tg, config = harness.install(data_dir)
plan = json.loads(open(plan_path).read())
snap = json.loads(open(plan['snapshot_path']).read())
import copy
t.snapshot = lambda: copy.deepcopy(snap)
sys.argv = ['tickets', 'send', '--plan', plan_path]
while time.time() < start_at:
    time.sleep(0.001)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    code = t.main()
open(out_path, 'w').write(json.dumps({'pid': os.getpid(), 'exit': code, 'stdout': buf.getvalue().strip(), 'plan': plan_path}))
