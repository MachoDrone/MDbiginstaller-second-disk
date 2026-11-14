# Nosana installer: second-disk option

A tested release candidate based on [MDbiginstaller](https://github.com/MachoDrone/MDbiginstaller).
For **fresh Docker installations on native Ubuntu 22.04/24.04**. Requires Internet,
`curl`, and a normal user with sudo access. It prepares host prerequisites; it does
not install the Nosana node itself.

## Run

```bash
curl -fsSL https://raw.githubusercontent.com/MachoDrone/MDbiginstaller-second-disk/v0.1.0-rc1/installer-beta.sh -o installer-beta.sh && bash installer-beta.sh
```

1. Choose **2** for second-disk storage, then select the intended drive.
2. Type its displayed confirmation: **ERASE** formats an eligible blank disk;
   **USE** preserves an eligible existing ext4 filesystem.
3. Answer **y** to package installation and **NVIDIA Container Toolkit** for GPU hosting.
   Working drivers are kept; driver installation is a separate prompt.

Run as your normal user—do not put `sudo` before `bash`.

## Protection and testing

OS disks, busy/unsupported disks, and existing Docker installations are refused.
There is no automatic system upgrade or reboot. A missing data disk blocks Docker
startup while allowing Ubuntu to boot.

Passed **113 automated checks**, **15 interruption/recovery scenarios**, and real
Ubuntu 22.04/24.04 VM tests. Physical GPU driver installation was not tested.
[Test details and limitations](docs/VERIFICATION.md).

To verify storage: `bash installer-beta.sh --verify-storage`.
For interrupted setup before Docker installation: `bash installer-beta.sh --recover-storage`.
Recovery cannot undo formatting or roll back a completed installation.

[Copy-ready Discord post](docs/DISCORD-POST.md). Other inherited installer scripts
are unchanged; use only `installer-beta.sh` for this feature.
