**Docker on a second disk — installer fork**

For a **fresh install on Ubuntu 22.04/24.04**. Run this as your normal user with sudo access (`curl` required):

```bash
curl -fsSL https://raw.githubusercontent.com/MachoDrone/MDbiginstaller-second-disk/v0.1.0-rc1/installer-beta.sh -o installer-beta.sh && bash installer-beta.sh
```

- Choose **2**, then select your second drive.
- Check the drive carefully: **ERASE** formats it; **USE** preserves supported existing ext4 files.
- Answer **y** to package installation and **NVIDIA Container Toolkit** for GPU hosting. Driver installation is a separate prompt.

It excludes OS disks and stops on unsupported setups. No automatic system upgrade or reboot. This prepares prerequisites; install the Nosana node afterward.

Tested with **113 automated checks**, **15 interruption/recovery scenarios**, and Ubuntu 22.04/24.04 VMs. Physical GPU driver installation remains untested. Release candidate, fresh installs only.

README: https://github.com/MachoDrone/MDbiginstaller-second-disk
