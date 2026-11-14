#!/usr/bin/env python3
import sys, subprocess, pexpect
from pathlib import Path
p=Path(__file__).resolve().parent;name=sys.argv[1];phrase=sys.argv[2];toolkit=sys.argv[3]
subprocess.run([str(p/'ssh'), 'test \"$(hostname)\" = installer-lab && test \"$(lsblk -dno SERIAL /dev/$(test -e /dev/sda && echo sda || echo vda))\" = TESTROOT'], check=True)
with (p/'evidence'/f'{name}.txt').open('w') as log:
 child=pexpect.spawn(str(p/'ssh'),['-tt','bash /home/tester/installer-beta.sh'],encoding='utf-8',timeout=1800)
 child.logfile=log
 for prompt,answer in [('Storage choice [1/2, Enter = 1]: ','2'),('Choose a disk number, or press Enter to cancel: ','1'),('to continue: ',phrase),('Continue with package installation? [y/N]: ','y'),]:
  child.expect_exact(prompt);child.sendline(answer)
 child.expect(r'Install [^\r\n]*NVIDIA driver[^\r\n]*\[y/N\]: ');child.sendline('n')
 child.expect_exact('Install NVIDIA Container Toolkit for GPU containers? [y/N]: ');child.sendline(toolkit)
 child.expect(pexpect.EOF);child.close()
 print(name,'exit',child.exitstatus)
 print((p/'evidence'/f'{name}.txt').read_text()[-4000:])
 sys.exit(child.exitstatus if child.exitstatus is not None else 99)
