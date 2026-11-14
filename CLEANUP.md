# Installer development cleanup

- Repository: this directory, retained as the requested separate fork.
- VM owner: vm_verification agent. Scratch, cloud images, virtual disks, extracted
  QEMU binaries, keys, and logs: `/tmp/nosana-installer-vm`; its local CLEANUP.md
  records launches before they occur. Only disposable image files may be attached.
- Planned local tests: Python unittest and bash syntax checks; no host disks,
  host Docker services, package installs, mounts, or NVIDIA changes.
- Network access: public upstream and primary documentation; GitHub publication
  only to the new fork after verification. No paid resources.
- Final runtime/fault VM processes exited cleanly; PIDs and localhost ports 22292
  and 22293 were checked after shutdown. Coordinator independently found no
  matching QEMU process and no listener on either port.
- Removed `/tmp/nosana-installer-fault-vm` and `/tmp/nosana-installer-vm` on
  2026-11-13 at 20:47 America/New_York, after exporting sanitized evidence and
  reusable runners. Both paths were checked absent after removal. Removed images,
  downloaded packages/QEMU, cloud-init media, test private SSH keys, and raw logs.
  Their measured allocated size before removal was 8,372,453,376 bytes.
- Retained source, documentation, test runners and concise evidence in this
  repository. No real host disk, host Docker/NVIDIA service, or host OS package
  was changed. No paid rental was created.
