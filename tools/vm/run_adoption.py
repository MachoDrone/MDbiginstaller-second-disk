#!/usr/bin/env python3
import pexpect,subprocess,sys
from pathlib import Path
p=Path(__file__).resolve().parent
name,phrase=sys.argv[1:3]
command=sys.argv[3] if len(sys.argv)>3 else 'bash /home/tester/installer-beta.sh --storage-only'
subprocess.run([str(p/'ssh'),'test "$(hostname)" = installer-lab && test "$(lsblk -dno SERIAL /dev/sda)" = TESTROOT'],check=True)
with (p/'evidence'/f'{name}.txt').open('w') as log:
 c=pexpect.spawn(str(p/'ssh'),['-tt',command],encoding='utf-8',timeout=90);c.logfile=log
 c.expect_exact('or Enter to cancel: ');c.sendline(phrase);c.expect(pexpect.EOF);c.close()
 print((p/'evidence'/f'{name}.txt').read_text());print('EXIT',c.exitstatus)
 sys.exit(c.exitstatus if c.exitstatus is not None else 99)
