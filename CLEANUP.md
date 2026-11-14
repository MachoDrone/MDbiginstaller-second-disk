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

## RC2 adoption work

- Registered before launch: disposable VM scratch `/tmp/nosana-installer-rc2-vm`,
  owned by vm_verification agent, localhost SSH22292, only regular image-file
  disks. QEMU/packages extracted locally; no host package/service/disk changes.
- Source and concise evidence retained in this repository. Additional fault VM
  scratch is recorded below. Final cleanup completed below.
- vm_verification also registered its independent recovery VM under
  `/tmp/nosana-installer-rc2-fault-vm`, SSH22293. It owns that process and cleanup;
  its local log records launch and only image-file attachments.
- Registered before launch: disk_safety may run the bounded Ubuntu22 adoption
  matrix under `/tmp/nosana-installer-rc2-ubuntu22-vm`, SSH22294, using copies of
  vm_verification's immutable Ubuntu22 fixture images. The agent owns process,
  keys and scratch cleanup. No physical disks, GPU passthrough or live Nosana
  activation; no changes to the other VM owners' mutable images or ports.
- Ubuntu22 agent completed its bounded matrix and shut down all owned QEMU processes.
  PIDs 2736415, 2737775, 2738348, 2738806 absent; localhost22294 refused a
  connection. Scratch `/tmp/nosana-installer-rc2-ubuntu22-vm` retained for
  coordinator joint cleanup. Evidence: `tests/evidence/rc2-22-summary.json`.
- Registered one additional Ubuntu22 manual-no-fstab interruption/recovery gate
  before launch in the same owned scratch and port22294, using fresh overlays.
- Additional Ubuntu22 manual-no-fstab durable-write interruption gate passed
  with two exact recovery checks. QEMU PID2742464 exited and localhost22294
  refused connection. Scratch remains ready for joint cleanup. Evidence:
  `tests/evidence/rc2-22-manual-fault-summary.json`.
- Final coordinator verification found no task QEMU process and connection
  refused on ports22292,22293,22294. Removed all three registered rc2 VM scratch
  directories, including images, extracted packages/QEMU, seed media and test SSH
  keys; all three paths were checked absent. Allocated bytes removed:
  **23,464,304,640**. Exact UTC time and per-directory measurements are retained in
  `tests/evidence/rc2-coordinator-cleanup.json`. Concise evidence and reusable
  test runners remain in the repository. No host disk/service/package changes,
  GPU qualification, live Nosana node, wallet, staking or paid rental occurred.
