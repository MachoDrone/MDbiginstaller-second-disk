# Verification record

Date: 2026-11-13, America/New_York. Status: all planned local and disposable-VM
gates passed, including final artifact verification. Prepared as a private release
candidate; public visibility requires explicit owner approval.

Frozen candidate:

- `installer-beta.sh`: `934d7f8ac6fe6ba297f6a2f230afab475ab2a8b2acdf176e68fa6d31d314467d`
- `src/storage.py`: `15081b5aede08c06b6898e306dc8edfd04d7832d833d4c7ad7e4a42a298079d8`
- 113 unit/refusal/recovery/runtime-output/bundle tests passed, 0.206 seconds.
- Bash syntax, generated-bundle consistency, and ShellCheck 0.9.0 passed.
- Independent review: disk eligibility, transactions/recovery, and wrapper
  integration reviewed separately; reported issues below were fixed.

## Failures found and corrected before freeze

- Ubuntu 24.04 `swapon` has no `--json` option. Actual VM discovery failed before
  mutation; inventory now uses supported raw output.
- Missing safety fields, invalid capacities, and blank UUID observations were
  accepted by the first policy draft; independent fixtures now require refusal.
- Malformed signature lists and disagreement between probing tools were not
  rejected. Both now fail closed.
- Configuration creation could overwrite an administrator's concurrent file.
  New files now use atomic exclusive creation after journaling.
- Recovery after reboot could strand its own data directories; it now remounts
  the recorded UUID before cleanup. Recovery itself can be resumed after partial
  configuration removal and is idempotent once complete.
- Verification initially checked only the mounted filesystem. It now checks
  managed configuration, Docker's running root, and containerd's effective root.
- Actual containerd 2.4.2 prints a single-quoted TOML root. The original exact
  double-quote comparison failed; both valid forms are now accepted.
- A managed installed runtime could enter package-upgrade paths on rerun. It now
  verifies and exits. Default-disk choice cannot bypass an interrupted journal.
- Format success text incorrectly implied pre-existing files were preserved.
  Formatting and non-formatting success messages are now distinct.
- ShellCheck flagged a curly apostrophe in a shell prompt. That prompt alone was
  changed to ASCII after the initial bundle freeze. The initial full installer run used bundle
  `fb2226d30cfa0e62ae24c2fa67bf13d0fb0f0dd4d647c40862d3c9e45b236b18`.
- A final discovery check found fstab labels with escaped whitespace or quotes
  could miss the configured-disk exclusion. Parsing now follows libmount's
  byte-wise, single-pass escaping and quote rules, while preserving systemd's
  distinct crypttab semantics. Fourteen independent raw-inventory regressions
  cover spaces, tabs, quotes, UTF-8, backslashes, `#`, and both file formats.
  Only inventory parsing changed; the previously tested mutation, recovery,
  and startup-guard functions remain unchanged.

## Real VM results

| Gate | Result |
| --- | --- |
| Ubuntu 24.04.5, blank secondary virtio disk | Only chosen disk formatted; OS disk excluded; cancellation and wrong confirmation made no filesystem/configuration change |
| Ubuntu 22.04.5, existing single-partition NVMe ext4 | Full normal-user installer reached completion; existing files, hashes, owners, modes, symlink, filesystem UUID, and GPT preserved |
| Docker 29.9.3 + containerd 2.4.2 | Both roots on the selected filesystem; real BusyBox container and named-volume read/write; default OS-disk runtime roots absent |
| NVIDIA Container Toolkit 1.20.2 | Optional installation merged the NVIDIA runtime without losing Docker's data-root; no GPU driver or GPU workload test |
| Reboot, both Ubuntu versions | Services resumed; container-volume data and OS/storage invariants retained |
| Boot without data disk, both versions | OS reached normal startup/SSH; runtimes stayed stopped; no fallback writes to OS disk |
| Wrong filesystem and missing ownership marker | Runtime startup refused; restoring correct storage/marker preserved volume data |
| Managed installer rerun | Verified storage and exited without package/driver changes; package-state snapshots identical |
| Fifteen forced-death boundaries | All passed recovery twice and successful rerun; all 13 already-created filesystems retained UUID and injected user file; two cases rebooted before recovery |

Every real scenario checked `/etc/fstab`, OS partition-table dump, root filesystem
UUID, and an OS sentinel against the recorded baseline. Those checks remained
unchanged. Fault-matrix execution took **367.6 seconds**, excluding VM setup. The
tests used official Ubuntu images with verified published SHA256 values, QEMU KVM,
a 12 GiB OS image and a separate sparse 128 GiB data image. No host block devices
were passed through.

The Ubuntu 22.04 controller initially expected an outdated prompt. The original
installer process remained alive and was completed through that same terminal;
the controller failure and terminal correction are retained in the evidence.
This is not represented as a successful original controller exit. Subsequent
public-artifact verify/rerun invocations independently returned exit 0.

Exact candidate hashes and concise transcripts are retained in
[Ubuntu 22 evidence](../tests/evidence/e2e-ubuntu22.txt),
[Ubuntu 24 evidence](../tests/evidence/e2e-ubuntu24.txt),
[runtime summary](../tests/evidence/e2e-summary.json),
[fault matrix](../tests/evidence/fault-matrix.txt),
[final delivered-artifact results](../tests/evidence/e2e-final-delivery.json),
[final fault-VM verification](../tests/evidence/fault-final-bundle-verify.txt), and
[development failures](../tests/evidence/e2e-development-failures.txt).
Reusable runners are under [tools/vm](../tools/vm/README.md) and
[tests/run_vm_fault_matrix.py](../tests/run_vm_fault_matrix.py).

The full Ubuntu 22 run and fault matrix used helper
`f63aeb92c00cbcddc955ec29213f95f3e264ca9e788bfd8c8ac70d65ea20fd50`.
Only the final configuration-reference parser changed afterward, so unchanged
mutation/runtime evidence is reused. The new parser received its own regression
gate, and the final downloadable artifact passed focused lint and verification
against both retained managed installations and the final fault VM. No passing result is inferred merely
from a changed test count.

Remote Actions are disabled while the repository is private; no hosted CI run is
claimed. The same unit/bundle checks and shell lint passed locally/in disposable
VMs. Public creation was rejected by automatic approval review because the user
had not explicitly authorized public visibility; private backup is permitted.

## Limits

No live operator host, physical disk, paid GPU rental, host OS package install,
or real NVIDIA driver was used. Tests cannot prove arbitrary hardware safe or
eliminate a mistaken operator erase confirmation, failing disk, or concurrent
administrator changing the machine. Unsupported layouts deliberately stop.
Existing-host migration and physical GPU/Nosana job readiness are outside this
release. VM execution was on amd64, not every architecture. Package versions
fetched from official repositories may change; this
record identifies the versions actually tested.
