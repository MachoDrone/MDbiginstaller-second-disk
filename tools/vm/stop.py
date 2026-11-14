#!/usr/bin/env python3
from pathlib import Path
import os,subprocess,time
p=Path(__file__).resolve().parent;pid=int((p/'vm.pid').read_text())
subprocess.run([str(p/'ssh'),'sudo poweroff'],check=False,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
for _ in range(120):
 try:os.kill(pid,0)
 except ProcessLookupError:
  print('Guest stopped cleanly, PID '+str(pid)+' gone');break
 time.sleep(1)
else:raise SystemExit('Guest did not shut down within120 seconds')
