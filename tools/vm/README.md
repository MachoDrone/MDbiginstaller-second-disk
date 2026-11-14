# Disposable VM verification tools

These are test tools, not part of the installer or a production launcher. Copy this directory to a disposable scratch directory before use. They require Python 3 + pexpect, SSH, and locally extracted QEMU under `qemu-root` (`usr/bin/qemu-system-x86_64`, `usr/bin/qemu-img`, libraries and firmware). QEMU can be downloaded/extracted using `apt-get download` and `dpkg-deb -x`; the test run installed no host packages. Never attach host block devices.

Prepare an official Ubuntu cloud image, verify its SHA256 against the official `SHA256SUMS`, and create a writable qcow2 overlay `root.qcow2` backed by that image. Create a separate sparse qcow2 `data.qcow2`; the measured tests used a 12 GiB root and a 128 GiB data disk. Create a cloud-init `seed.iso` (volume label `cidata`) with a test-only `tester` user, passwordless sudo, hostname `installer-lab`, the public half of local `test_key`, and a `/home/tester/cloud-init-ready` marker. Never commit the test key or VM disk images.

`start.py` boots only existing regular image files directly inside the scratch directory and refuses a duplicate active VM. The OS disk has serial `TESTROOT`, extra disks have serial `TESTDATA1`, etc. Networking uses QEMU user-mode NAT and SSH binds only to localhost (port22292, or `NOSANA_VM_PORT`). The VM uses KVM and 1536 MiB RAM. If KVM is unavailable, explicitly change the accelerator to TCG and expect slower boot. Only test SSH host-key verification is disabled because disposable guest host keys change between scenarios.

Commands from the scratch directory:

```sh
./start.py root.qcow2 data.qcow2
./wait_guest.py
./scp /path/to/installer-beta.sh tester@127.0.0.1:/home/tester/installer-beta.sh
mkdir -p evidence
./run_bundle.py fresh-24 'ERASE TESTDATA1' n
./ssh 'sudo docker info'
./stop.py
./start.py root.qcow2 NONE  # missing-disk boot
```

`run_bundle.py` exercises the full normal-user bundle: second-disk choice, selected drive1, supplied confirmation phrase, package install yes, driver install no, and caller-selected NVIDIA toolkit answer. NVMe identifiers may be WWNs instead of serials: use the exact observed confirmation for that known fixture. Its log is evidence/NAME.txt. `run_interactive.py` exercises the root storage helper at `/home/tester/storage.py` for cancel/confirmation cases. Both verify the expected test hostname and root-disk serial before running.

Use `NOSANA_TEST_DISK_BUS=nvme ./start.py root22.qcow2 data22.qcow2` for native guest NVMe discovery. Only prepare ext4/GPT fixtures from *inside* the verified disposable guest. Record original FS UUID, partition table, content hashes, ownership, modes and symlink targets before selecting reuse. Assert these remain identical after install/reboot.

Always cleanly stop the guest and verify its PID exited before copying/restoring images. Keep immutable pristine baseline copies. `stop.py` requests guest poweroff and waits at most120 seconds. Preserve concise sanitized test evidence, stop all QEMU processes, verify localhost ports released, and remove or explicitly retain scratch images in CLEANUP.md. The scripts never create GPU rentals, modify host services, or install host packages.
