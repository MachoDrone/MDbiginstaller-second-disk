# RC2 verification record

Status: local and disposable-VM gates passed for the final release candidate.
Publication checks are recorded with the
[rc2 release](https://github.com/MachoDrone/MDbiginstaller-second-disk/releases/tag/v0.1.0-rc2).
This record covers adoption of prepared storage and Nosana preparation. The
[rc1 verification](VERIFICATION.md) remains the record for the original spare-disk
implementation; rc1 is an immutable, separate release.

## Scope and evidence

The operator reported a fresh install, Secure Boot disabled, Ubuntu/EFI on a
119.2 GiB SATA disk and a whole 476.9 GiB NVMe ext4 filesystem already mounted at
`/var/lib/docker`. Tests reproduce the relevant topology using isolated Ubuntu
22.04/24.04 VMs, a SATA OS disk and separate whole NVMe image files. They do not
access the operator's machine or any physical host block device.

Adoption preserves the existing mount and files. A targeted, journaled fstab
change adds UUID-based persistence, `nofail`, and bounded device/mount waits.
Docker and containerd use distinct directories beneath the adopted disk's
`nosana-runtime`. Existing rc1 paths remain supported under `/mnt/nosana`.

Nosana preparation installs pinned official images and node software into Docker
storage, with persistent state under `nosana-runtime/nosana-state`. It never
starts a node, creates a wallet, registers, qualifies, stakes or spends funds.
All software/cache preparation runs before a possible driver reboot. Explicit
startup is separately guarded and explained to the operator.

## Development failures retained

- The first modular bundle imported Python as root, leaving root-owned bytecode
  cache files that its normal-user cleanup trap could not delete. Both extracted
  Python entrypoints now use `-B`. The original cancellation/adoption outputs
  retain that failure; storage preservation is not presented as a successful
  wrapper exit.
- Docker 29 reports an absent volume with lowercase `no such volume`; the first
  Nosana helper recognized only uppercase `No such`. It stopped before pulling
  or writing anything. Missing-object detection now recognizes the observed
  wording while independently refusing daemon/socket failures.
- Independent review found that a previously created cache volume and inner
  container needed their complete storage/launch definitions checked on reuse,
  rather than relying on ownership labels and image identity alone. The checks
  now require exact definitions and revalidate after create-by-name; independent
  fixtures cover changed bindings and a concurrent foreign volume.
- An independent malformed-UUID fixture exposed permissive adoption UUID syntax.
  Adoption now requires the canonical 8-4-4-4-12 hexadecimal form.
- Real image inspection exposed an implicit rootless-data volume declared by the
  official Podman image, beyond the explicitly requested mounts. The original
  exact mount-set check would refuse that legitimate container. The final check
  narrowly allows the image's independently verified local anonymous volume;
  arbitrary extra mounts remain unsupported.
- Docker29 reports a manifest/index image ID, while Podman uses a configuration
  digest. Real transfer established that Podman's `Digest` retains Docker's
  manifest ID. The final resolver requires a unique configuration ID matched by
  ID or digest, then checks the imported image's labels and pinned environment.
- Two test-harness assumptions were corrected without changing production code:
  a stale SSH session after reboot needed bounded liveness/timeouts, and a
  read-only-disk test expected an inactive service even though containerd's
  packaged restart policy reports `activating/auto-restart`. The retained evidence
  shows guard failure and `MainPID=0`, with no running daemon or fallback writes.
  The original failures were retained and the existing fixtures resumed.

## Frozen local candidate

- Bundle: `ffed7c68885bd08aa5381c49ebb7377facba5abc76371c5ca3686a14252a3bea`.
- Storage helper: `cf5baf32cf02ec78d196b1d935b95063cd5dcbf0ee2f987ef2b82d27a9732a0b`.
- Adoption helper: `42dc68ec3eea015370d627ab625bd0bde1cb9a76072e3fe0c0e90ba0895245ea`.
- Nosana helper: `37492424c62db3a52ad285656cb6a6135a771e14371e061637552623e9d898d2`.
- 200 automated tests passed in 1.957 seconds: original113, adoption53,
  routing6, Nosana28. Bash syntax, generated-artifact consistency and whitespace
  checks passed for source/docs/tests/tools. Raw terminal transcripts retain
  their carriage returns and progress spacing. All three extracted helpers are byte-identical to source and
  compile. [Local record](../tests/evidence/rc2-final-local.json).
- Independent adoption and Nosana storage/resume reviews reported no outstanding
  findings after the documented fixes. Package and startup contracts are recorded
  separately in [the source review](NOSANA-CONTRACT.md).

## Real VM evidence

Ubuntu22 matrix: **PASS**, 421.6 seconds, final storage/adoption source hashes
above. Both manual and existing-fstab adoption preserved UUID, original files,
ownership/modes/symlink, existing mount identity, OS partition table/root UUID
and OS sentinel. Actual Docker29.9.3 and containerd2.4.2 roots, an Alpine
container, and persistent named-volume I/O were on the selected NVMe filesystem.
Correct-disk reboot restored services and data. Missing-disk boot reached SSH;
explicit runtime starts failed and the OS `/var/lib/docker` remained empty.
All owned guests stopped and SSH22294 was released.
[Summary and evidence](../tests/evidence/rc2-22-summary.json).

Manual no-fstab fault/recovery also passed in **5.54 seconds**: a real abrupt exit
immediately after the durable fstab append, before journal stage update, followed
by recovery twice. The original raw no-entry fstab bytes/mode, mount ID, UUID,
files and OS invariants matched exactly; no runtime/configuration residue remained.
[Fault evidence](../tests/evidence/rc2-22-manual-fault-summary.json).

The full normal-user Ubuntu24 installation completed with exit0, unchanged boot
ID (no intermediate reboot), Docker29.9.3, containerd2.4.2, NVIDIA Toolkit1.20.2,
and prepared Nosana software. It reused existing unmounted ext4 under the original
`/mnt/nosana` path and preserved the existing files, filesystem/OS identities and
original fstab. This run used bundle `ac22ecd72491d3772884c88efcd77103f328bcc9418e6467e0604b8ec6727be9`,
before the two real launch-contract corrections above.

Independent AST/byte comparisons show that storage, adoption, the outer shell,
Nosana preparation/build/guard/write functions and all module declarations are
unchanged between that full run and the final artifact. Only launch image/volume
validation and the new image-ID resolver changed; their actual runtime tests and
final-artifact checks supply the new evidence. The earlier full run is not
misrepresented as an identical artifact.
[Exact comparison](../tests/evidence/rc2-full-to-final-path-comparison.json).

The final bundle passed managed rerun, all four preparation/verification flags,
temporary-directory cleanup and ShellCheck, with package inventory and boot ID
unchanged during rerun. A later cold reboot preserved the real volume and Nosana
preparation. These final checks took **31.17 seconds**.
[Final artifact results](../tests/evidence/rc2-full24-final-result.json).

Ubuntu24 adopted-storage tests also passed ordinary-fstab and manual adoption,
original blank-disk preparation, and three abrupt-exit recovery boundaries
(after fstab, after ownership marker with reboot, and after runtime config).
Recovery restored exact fstab bytes/mode and preserved mounted storage/user/OS
data; later administrator edits were refused without overwriting anything.
[Storage summary](../tests/evidence/rc2-24-summary.json) ·
[Recovery evidence](../tests/evidence/rc2-fault-summary.json).

Read-only storage blocked both runtimes and Nosana verification. A wrong-UUID
filesystem explicitly mounted at the managed target was refused by identity,
and its files were preserved. Wrong/absent-disk cold boots reached SSH in
**16.32/16.12 seconds** in these VMs, without runtime writes falling back to the
OS disk. Restoring the correct disk preserved the container volume and all
original file/OS/fstab invariants.
[Negative cases](../tests/evidence/rc2-24-19-storage-negative-results.json).

Real Docker-to-Podman image transfer took **49.35 seconds**. A harmless inner
container installed **505 packages, node version1.1.91**, with networking disabled
in **14.63 seconds**. The exact production inner definition was created and
inspected through the final validator, never started. The outer anonymous-volume
check used real volume observations with explicitly documented CPU-fixture field
normalization; it does not establish GPU activation. Final bundle preparation
and verification each passed twice; no wallet, node startup or temporary-directory
leak occurred. Fixture containers were removed.
[Nosana evidence](../tests/evidence/rc2-nosana-summary.json) ·
[Exact proof boundary](../tests/evidence/rc2-nosana-final-predicates.txt).

See [the registered plan](PLAN-RC2.md). Successful rc1 evidence is reused only
for unchanged code and relevant contracts. No live host or paid rental was used.

## Limits

These VMs have no NVIDIA GPU. Driver installation, GPU passthrough, qualification,
mainnet registration, paid jobs, staking and live Nosana execution are not
verified. CPU-only container checks establish storage and runtime API behavior,
not GPU/job readiness. The release remains a candidate, with explicit operator
activation after any required reboot.

The wizard supports fresh rootful Docker installs and simple ext4 secondary
filesystems. It does not migrate existing Docker/Podman/Nosana state. Recovery
refuses completed or runtime-installed storage and administrator changes. For
adoption, recovery restores the original fstab and therefore its original boot
behavior, including a previously required data-disk mount. Formatting a selected
blank disk cannot be undone by configuration recovery.

Ubuntu, system packages, NVIDIA drivers, small system configuration and normal OS
logs remain on the OS disk; relocating workload storage does not guarantee that
an OS disk can never fill.
