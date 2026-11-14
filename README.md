# Nosana + Docker on a second disk

For **fresh installs on native x86-64 Ubuntu 22.04/24.04**, based on
[MDbiginstaller](https://github.com/MachoDrone/MDbiginstaller).
Docker/containerd data, Nosana state and workload caches use the second disk.
Ubuntu packages and NVIDIA drivers stay in their normal OS locations.

## Install

Run as your normal Ubuntu user with sudo access (`curl` required):

```bash
curl -fsSL https://raw.githubusercontent.com/MachoDrone/MDbiginstaller-second-disk/v0.1.0-rc2/installer-beta.sh -o installer-beta.sh && bash installer-beta.sh
```

1. Choose **2** and check the displayed drive identity.
2. **USE** preserves an eligible ext4 filesystem, including one already mounted
   at `/var/lib/docker`. **ERASE** formats an eligible blank disk.
3. Accept package installation and NVIDIA Container Toolkit. Install a driver
   if needed; working drivers are kept.

Everything is prepared **before any required driver reboot**. No automatic reboot
or Nosana network activation occurs. After any needed reboot:

```bash
bash installer-beta.sh --start-nosana
```

Use this launcher to retain the selected storage paths. It checks GPU readiness
and asks before starting registration/qualification and signed account setup.

## Checks and recovery

OS disks and existing unmanaged runtime installations are refused. Missing or
wrong storage blocks runtime startup while allowing Ubuntu to boot.

- `--verify-storage`: check disk and runtime configuration.
- `--verify-nosana`: check prepared Nosana software.
- `--prepare-nosana`: resume Nosana preparation.
- `--recover-storage`: recover interrupted storage setup before runtime installation.

Recovery cannot undo formatting or roll back a completed installation.
[Verification and limitations](docs/VERIFICATION-RC2.md) ·
[Discord post](docs/DISCORD-POST.md).
200 automated checks and Ubuntu 22.04/24.04 VM tests passed; physical GPU/job
qualification remains unverified.
Use `installer-beta.sh`; inherited scripts do not provide this feature.
