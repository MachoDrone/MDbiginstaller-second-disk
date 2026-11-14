# Nosana installer with a second-disk option

An experimental derivative of [MachoDrone/MDbiginstaller](https://github.com/MachoDrone/MDbiginstaller),
with an interactive storage wizard and a conservative fresh-install policy.
The upstream history is retained. Only `installer-beta.sh` is the new supported
entry point; the other inherited installers are historical and unchanged.

**Tested release candidate; public publication awaits owner approval.**
Verification status and limits are in [VERIFICATION.md](docs/VERIFICATION.md).
This prepares Docker and optional NVIDIA prerequisites. It does not install or
launch the Nosana node itself.

## Run

The following one-command install becomes available after public publication and
creation of the tested `v0.1.0-rc1` tag. Until then, this private repository is the
review copy; do not send this command to an operator yet.

On **native Ubuntu 22.04 or 24.04**, open a terminal as your normal user and run:

```bash
curl -fsSL https://raw.githubusercontent.com/MachoDrone/MDbiginstaller-second-disk/v0.1.0-rc1/installer-beta.sh -o installer-beta.sh && bash installer-beta.sh
```

Do not add `sudo` before `bash`; the installer asks for sudo when needed. Do not
pipe the download into a shell: the disk wizard needs an interactive terminal.

1. Choose the OS disk or a spare disk for Docker storage.
2. For a spare disk, choose from the numbered list showing model, size, and serial
   or WWN. OS disks and busy disks are excluded. If formatting is necessary, type
   the displayed `ERASE <disk ID>` confirmation. Formatting destroys any existing
   data, including data that filesystem probes cannot recognize.
3. Confirm installation of Docker packages. Existing working NVIDIA drivers are
   kept. Driver installation/replacement and NVIDIA Container Toolkit installation
   have separate prompts, defaulting to no.

There is no automatic whole-system upgrade or reboot. Log out/in for Docker group
membership, and reboot when appropriate if you chose to install a driver.

## Supported storage

- **Fresh Docker/containerd installs only.** Existing images, containers, volumes,
  runtime packages, or unmanaged runtime configuration are refused. There is no
  automatic migration, deletion, or package removal.
- A dedicated, nonremovable secondary SATA/NVMe disk with a unique serial or WWN.
  Virtual virtio disks are supported for verification.
- An unpartitioned disk with no detected signatures can be formatted as a
  whole-disk ext4 filesystem after explicit confirmation. No partition table is
  created, and no force-format or signature-wiping option is used.
- A clean, unmounted ext4 filesystem on the whole disk or its sole partition can
  be reused. Existing files are retained; the wizard adds `nosana-runtime`.
- Mounted disks, fstab/crypttab-referenced disks, active swap, USB/removable media,
  readonly disks, RAID/LVM/encryption/multipath, multiple partitions, dirty ext4,
  missing identities, conflicting signatures, and ambiguous inventories are
  refused. No override is offered.

The wizard does not infer that a drive contains nothing valuable just because
it lacks a known filesystem signature. Select only the drive you intend to use.
Capacity and free space must also be suitable for the workloads you intend to run.

## What changes

The disk mounts at `/mnt/nosana`. Docker uses
`/mnt/nosana/nosana-runtime/docker`; containerd uses
`/mnt/nosana/nosana-runtime/containerd`. Both locations matter for fresh Docker
29+ installations using the containerd image store.

The wizard creates its own `mnt-nosana.mount` systemd unit and dependencies for
Docker and containerd. It does **not** edit `/etc/fstab` or the bootloader, or
repartition/format a system disk. Runtime startup checks require the
expected UUID, writable ext4 mount, owned directories, and matching ownership
marker. If the disk is missing, Ubuntu can boot, while these services stay stopped
instead of filling the OS disk. Reconnect the correct disk and reboot to retry
normal startup.

Configuration writes are journaled before creation and atomic. Existing
configuration is never replaced by the storage wizard, including when another
process creates it during setup. Re-running a completed setup verifies it without
formatting again. If Docker/containerd is already installed on that managed setup,
the full installer verifies storage and exits without package or driver changes.

## Inspect, verify, or recover

Use the same downloaded file:

```bash
bash installer-beta.sh --list-disks
bash installer-beta.sh --storage-only
bash installer-beta.sh --verify-storage
```

If setup was interrupted, the next run refuses to guess. Recover its recorded
configuration with:

```bash
bash installer-beta.sh --recover-storage
```

Recovery is for **unfinished setup before a runtime was installed**. It removes
only matching installer-owned configuration and empty directories bearing its
ownership marker. It refuses later administrative edits, unexpected data, or an
unavailable disk needed for cleanup. Reconnect that disk before retrying. Recovery
cannot undo formatting, and never erases a prepared filesystem; a subsequent run
can reuse that filesystem without formatting again. A completed installation may
contain live data and is not automatically rolled back. A disk interrupted during
formatting may need separate inspection; no forced repair is attempted.

## Development and evidence

The downloadable shell script embeds `src/storage.py`, so it never fetches a
second mutable helper during privileged execution. To rebuild and verify:

```bash
python3 tools/build.py
python3 -m unittest discover -s tests -v
bash -n installer-beta.sh
python3 tools/build.py --check
shellcheck -S warning installer-beta.sh
```

Unit tests never operate on real disks or services. Real integration proof uses
disposable Ubuntu VMs with separate virtual OS/data devices, real ext4/systemd,
Docker containers, reboot/missing-disk scenarios, and injected process deaths.
See the [test record](docs/VERIFICATION.md), [scope and gates](docs/PLAN.md), and
[cleanup record](CLEANUP.md). A VM without an NVIDIA GPU cannot validate physical
GPU drivers or Nosana job readiness.

Primary contracts: [Docker data directories](https://docs.docker.com/engine/daemon/#daemon-data-directory),
[Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/),
[systemd mounts](https://www.freedesktop.org/software/systemd/man/latest/systemd.mount.html),
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
