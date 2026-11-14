#!/usr/bin/env python3
import sys, subprocess, pexpect
from pathlib import Path
p=Path(__file__).resolve().parent
name=sys.argv[1]
answers=sys.argv[2:]
subprocess.run([str(p/'ssh'), 'test \"$(hostname)\" = installer-lab && test \"$(lsblk -dno SERIAL /dev/$(test -e /dev/sda && echo sda || echo vda))\" = TESTROOT'], check=True)
with (p/'evidence'/f'{name}.txt').open('w') as log:
 child=pexpect.spawn(str(p/'ssh'), ['-tt','sudo python3 /home/tester/storage.py'], encoding='utf-8', timeout=90)
 child.logfile=log
 child.expect('Choose a disk number, or press Enter to cancel: ')
 child.sendline(answers[0])
 if answers[0] and len(answers)>1:
  child.expect('to continue: ')
  child.sendline(answers[1])
 child.expect(pexpect.EOF)
 child.close()
 print(f'{name}: exit {child.exitstatus}')
 print((p/'evidence'/f'{name}.txt').read_text())
 sys.exit(child.exitstatus if child.exitstatus is not None else 99)
