**Docker + Nosana on your second disk**

Fresh x86-64 Ubuntu 22.04/24.04 install. Run as your normal user with sudo access:

```bash
curl -fsSL https://raw.githubusercontent.com/MachoDrone/MDbiginstaller-second-disk/v0.1.0-rc2/installer-beta.sh -o installer-beta.sh && bash installer-beta.sh
```

- Choose **2** and check the drive identity.
- **USE** preserves ext4, including a disk already mounted at `/var/lib/docker`.
- **ERASE** formats a blank disk—check carefully.
- Accept package installation and NVIDIA Container Toolkit; install a driver if needed.

Docker, Nosana state and workload caches go on that disk. NVIDIA drivers stay with Ubuntu. Everything installs before any required reboot.

After any needed reboot, run:

```bash
bash installer-beta.sh --start-nosana
```

This checks the GPU and asks before activating the host. No automatic reboot or activation. Release candidate; VM storage/setup tests do not prove physical GPU qualification.

README: https://github.com/MachoDrone/MDbiginstaller-second-disk
