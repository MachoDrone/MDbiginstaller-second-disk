#!/usr/bin/env python3
from pathlib import Path
import subprocess,time,sys
p=Path(__file__).resolve().parent;deadline=time.monotonic()+120
cmd=sys.argv[1] if len(sys.argv)>1 else 'test -f /home/tester/cloud-init-ready'
while time.monotonic()<deadline:
 try:
  r=subprocess.run([str(p/'ssh'),cmd],capture_output=True,text=True,timeout=15)
 except subprocess.TimeoutExpired:
  time.sleep(2)
  continue
 if r.returncode==0:
  print(r.stdout);break
 time.sleep(2)
else:raise SystemExit('Guest check did not succeed within 120 seconds; inspect serial.log')
