# RC2: adopt a prepared Docker disk

Authorized 2026-11-13. Outcome: preserve rc1's spare-disk paths and support a
fresh-runtime ext4 secondary disk already mounted at `/var/lib/docker`.
Clarified scope: install Docker and Nosana on the second disk, with NVIDIA
drivers and Ubuntu packages in their normal OS locations. Complete all
installation steps before any driver reboot; do not require a working loaded
GPU driver merely to prepare Nosana. No automatic reboot.
Operator evidence: separate SATA Ubuntu/EFI disk and whole NVMe ext4 mounted at
that path; Docker/Nosana reported fresh, Secure Boot reported disabled. No live
host access or installation is authorized; use isolated VMs only.

Scope: detect and explicitly adopt that exact mount, without formatting,
unmounting, relocating files, repairing a filesystem or migrating a runtime.
Create separate Docker/containerd directories beneath an owned namespace on the
mounted disk. Refuse existing runtimes/stores, OS ancestry, ambiguous identity,
foreign mounts, unsafe paths, and conflicting mount configuration.

Install the documented classic Nosana Podman-in-Docker host using fixed official
image digests and a fixed node version. Place its persistent state, wallet/config,
inner runtime cache, images and workload volumes on selected storage. Prepare it
stopped; starting a mainnet node can create signed accounts and register/qualify
the host, so startup is a separately explained, explicit operator action. Test
preparation with real images, storage placement, safe reruns and refusal paths;
never start/register a live Nosana host in test VMs. GPU readiness is checked on
explicit launch after any needed reboot, not during installation.

Existing mount persistence is a new supported contract. Back up and journal a
targeted `/etc/fstab` change (or addition) before atomic replacement: UUID source,
nofail, bounded device wait. Preserve unrelated lines and existing safe options.
systemd's generated mount and runtime startup guards must keep missing storage
from blocking OS boot or writing fallback runtime data. A native mount override
alone cannot remove hard dependencies generated from an old fstab entry.
Recovery restores exact prior fstab bytes/mode only if the file has not been
changed by another administrator; it preserves adopted data and never unmounts
the existing disk. Completed rc1 journals remain readable.

Ranked hypotheses / discriminating evidence:
1. Prepared mount could be OS-backed or ambiguous: exact-mount, physical ancestry,
   UUID, whole-filesystem and single-use policy fixtures; actual SATA+NVMe VM.
2. Existing fstab can break boot: ordinary defaults/pass2 entry and manual-mount
   fixtures; present, absent, wrong disk reboot with OS login and no fallback.
3. Adoption could destroy files/config: before/after UUID, table, file hash/mode,
   untouched mount and unrelated fstab byte comparisons; injected interruptions.
4. containerd could remain on OS disk: real Docker image/container/volume writes
   and effective roots on adopted filesystem.
5. Shared changes could regress rc1: original113 tests, completed rc1 journal
   compatibility, focused real blank/unmounted-reuse regressions.
6. Nosana could leave persistent state on OS or launch during install: assert
   exact stopped container spec, NVMe state bind and named volume filesystem,
   pinned image identities, no start/exec/wallet creation during preparation,
   idempotent interrupted retries, foreign-container/config refusal and missing
   storage guard. Real GPU/job acceptance remains unverified without hardware.

Required proof: independent policy/transaction tests, narrow independent review,
real Ubuntu22/24 mount/reboot/runtime/recovery tests, source freeze before final
gates, public CI and raw-download hash. Reuse unchanged rc1 evidence where code
and relevant inputs match. Keep failures and source hashes explicit. Publish a
new prerelease tag (rc2); do not move rc1. Public publication already authorized.
The final normal-user bundle must complete storage, Docker package installation,
NVIDIA Toolkit and Nosana preparation in one invocation without an intermediate
reboot (GPU driver declined in the GPU-less VM). This proves shell integration
and the user's clarified installation order; it does not prove driver loading.
