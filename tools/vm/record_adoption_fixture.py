#!/usr/bin/python3
"""Read-only guest fixture record; no production imports."""
import hashlib,json,os,stat,subprocess
from pathlib import Path
assert os.geteuid()==0
run=lambda args:subprocess.run(args,check=True,capture_output=True,text=True).stdout
assert run(['hostname']).strip()=='installer-lab'
assert run(['lsblk','-dno','SERIAL','/dev/sda']).strip()=='TESTROOT'
base=Path('/var/lib/docker')
files={}
for name in ('operator-keep.txt','operator-notes/keep.bin','operator-link','lost+found'):
 p=base/name;s=p.lstat();item=dict(uid=s.st_uid,gid=s.st_gid,mode=oct(stat.S_IMODE(s.st_mode)))
 if p.is_symlink():item['target']=os.readlink(p)
 elif p.is_file():item['sha256']=hashlib.sha256(p.read_bytes()).hexdigest();item['size']=s.st_size
 files[name]=item
print(json.dumps(dict(os_release=Path('/etc/os-release').read_text(),os_partition_table=run(['sfdisk','--dump','/dev/sda']),os_root_uuid=run(['blkid','-s','UUID','-o','value','/dev/sda1']).strip(),os_sentinel_sha256=hashlib.sha256(Path('/root/os-preserved').read_bytes()).hexdigest(),fstab=Path('/etc/fstab').read_text(),fstab_mode=oct(stat.S_IMODE(Path('/etc/fstab').stat().st_mode)),fstab_uid=Path('/etc/fstab').stat().st_uid,fstab_gid=Path('/etc/fstab').stat().st_gid,mount_id=run(['findmnt','--mountpoint','/var/lib/docker','--noheadings','--output','ID']).strip(),data_uuid=run(['blkid','-s','UUID','-o','value','/dev/nvme0n1']).strip(),data_files=files,mount=run(['findmnt','--json','--mountpoint','/var/lib/docker','--output','SOURCE,TARGET,FSTYPE,OPTIONS,UUID'])),indent=2))
