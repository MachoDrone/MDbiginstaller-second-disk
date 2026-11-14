# Second-disk installer plan

Started 2026-11-13 (America/New_York). Upstream: MachoDrone/MDbiginstaller,
commit cf19e865d3eaca435c4dce31be2ad98d3e5fe4ba.

Outcome: a separate, reviewable fork whose interactive installer can prepare a
spare disk for Docker and containerd without requiring the operator to configure
Linux storage. No production host or game work is authorized by this task.

Scope: Ubuntu 22.04/24.04, fresh Docker installation. Preserve default-disk choice.
Only positively identified, unmounted, simple secondary disks qualify. Reuse an
ext4 filesystem without formatting; formatting is limited to an unpartitioned
disk with no detected signatures, after an explicit identity confirmation.
Absence of signatures does not prove absence of valuable data. Reject system,
swap, removable, read-only, RAID/LVM/crypt/multipath, ambiguous or changed devices.
Existing runtime data/configuration requires a separate migration and is refused.
No automatic full OS upgrade, driver replacement, or reboot.

Ranked hazards and discriminating proof:

1. Wrong disk or existing data changed: independent topology fixtures, real VM
   OS-disk refusal, ext4 sentinel checks, exact selected-device identity recheck,
   no destructive command before a typed confirmation, no force/wipe options.
2. Boot fails or missing disk silently fills OS disk: real reboot with disk
   attached, then absent; OS must boot while Docker/containerd refuse to start.
3. Docker 29 image data stays on OS disk: real Docker image/container/volume test
   proves both Docker and containerd roots are on the selected filesystem.
4. Partial failure corrupts configuration: journal before writes, atomic writes,
   config backup/recovery, fault injection at each mutation boundary, rerun tests.
5. UI/integration bypasses safety: standalone bundled artifact consistency,
   noninteractive refusal, invalid selection/cancellation, shell syntax checks.

Required final gates: focused unit/negative/failure tests, independent code review,
real disposable Ubuntu VM storage/systemd/Docker tests, candidate frozen before
final verification. Record exact tested commits/hashes and limitations. VM proof
does not establish real NVIDIA-driver compatibility. Unsupported environments
must fail closed. Do not label incomplete VM proof as ready for an operator.

Publication: separate repository MachoDrone/MDbiginstaller-second-disk (GitHub
cannot fork a repository into its current owner's same namespace as itself).
Retain upstream history and attribution. Publish as experimental with measured
test evidence; do not change upstream, install on a real host, or send a message
to the operator. User explicitly requested this fork and thorough testing.
