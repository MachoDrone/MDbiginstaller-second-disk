# Nosana installation and storage contract

This fork prepares the **classic GPU host, Podman inside Docker**, documented in
[Running the Host](https://learn.nosana.com/hosts/grid-run). It does not install the
job-submission CLI as a substitute for a host. Preparation and activation are
separate; no GPU node, registration, benchmark, wallet or transaction is started
while installing the software.

## Reviewed upstream inputs

Reviewed on 2026-11-14:

| Input | Fixed identity |
| --- | --- |
| Current [official classic launcher](https://nosana.com/start.sh) | Script version `1.1.2-3`; fetched bytes SHA-256 `4e417c1f018cd71b74c151ec70d9bccccde67b59b9c69fc724444d632458f75b` |
| [Nosana node v1.1.91](https://github.com/nosana-ci/nosana-node/releases/tag/v1.1.91) | Commit `997ce206d769230443ba53da52fd539b224880be` |
| [Podman image metadata](https://hub.docker.com/v2/repositories/nosana/podman/tags/v1.1.2) | `docker.io/nosana/podman@sha256:1ea1474e0ee2e909246180e2598c55ef40d4cf30e5c310db77c4636f89e2c3d6`; Linux amd64 manifest `sha256:5173319c02ee5e300c621aca907815aa8c08a25eece5fa94dec451a01d426bfc` |
| [Node-wrapper image metadata](https://hub.docker.com/v2/repositories/nosana/nosana-node/tags/latest) | `docker.io/nosana/nosana-node@sha256:2bc2f2a0a559f54abdc21505f491a1ab23b9e2e4e8bf74164eddfccca91a64d3`; Linux amd64 manifest `sha256:b46fdffeabd121f25e7794dcb0f79d08591d1a635df2603d207e890fa2287b18` |
| [Published npm package](https://registry.npmjs.org/@nosana/node/1.1.91) | `@nosana/node@1.1.91`; integrity `sha512-C8b0dAbMI5y3zevbcOtN5OIuPEmSU+PcbZUD3zsrIjG6orENymsnanheNywGvBxpQOV5C8VKdoEbIF2jY1TOZw==` |

The mutable official script is **read as a reference, never downloaded and
executed by this fork**. Image downloads use the recorded digests and
`linux/amd64`. Activation uses `--pull=never`; it never silently switches to a
new image or package version. The official compatibility value
`NOSANA_START_SCRIPT_VERSION=1.1.2-3` is retained. Fork ownership is recorded in
separate container/image/volume labels.

## Paths

`DATA` means `/mnt/nosana/nosana-runtime` for the original spare-disk flow, or
`/var/lib/docker/nosana-runtime` for adoption of the prepared Docker mount.

| Contents | Physical host location / runtime mapping |
| --- | --- |
| Docker images, build cache, containers and ordinary local volumes | `DATA/docker` |
| containerd content and snapshots | `DATA/containerd` |
| Node wallet and persistent state | `DATA/nosana-state`, bound as `/root/.nosana` in the outer Podman container and inner node |
| Wallet after explicit first activation | `DATA/nosana-state/nosana_key.json` |
| Node database and job control files | `DATA/nosana-state/nosana_db.json` and `DATA/nosana-state/jobs/…` |
| Podman images, layers, workload/model volumes | Docker local volume `podman-cache`: `DATA/docker/volumes/podman-cache/_data`, mounted at outer `/var/lib/containers`; Podman graph root `/var/lib/containers/storage`, volume root `/var/lib/containers/storage/volumes` |
| Image-declared rootless Podman volume | The official image also declares `/home/podman/.local/share/containers`; Docker creates an anonymous local volume under `DATA/docker/volumes`. Its actual driver, options and path are verified; unrelated extra mounts are refused. |
| Installed node package and npm cache | Prepared image layers in `DATA/docker`; after activation, inner image/layers in `podman-cache`, with `/root/.nosana-node/1.1.91` and `/root/.npm` inside the node |
| Small setup manifest and guarded launcher | `DATA/nosana-setup.json`, `DATA/nosana-launch.py` |
| NVIDIA driver, kernel module, Container Toolkit, Docker/system binaries | Their normal Ubuntu package locations on the OS disk |
| Runtime sockets and NVIDIA persistence runtime directory | Volatile `/run` and container runtime paths; these are not persistent model stores |

The node's [supported options](https://github.com/nosana-ci/nosana-node/blob/997ce206d769230443ba53da52fd539b224880be/src/cli/commands/start/command.ts)
include `--config`, `--wallet` and its Podman socket path. The config directory
and wallet path are separate options. We keep upstream paths inside the
container and change the host bind source; no invented storage flag or changed
`HOME` is needed. The official shell launcher itself has no data-directory flag.

The [database implementation](https://github.com/nosana-ci/nosana-node/blob/997ce206d769230443ba53da52fd539b224880be/src/NodeManager/db/index.ts)
and [job session paths](https://github.com/nosana-ci/nosana-node/blob/997ce206d769230443ba53da52fd539b224880be/src/NodeManager/node/job/JobRuntimeSession.ts)
establish the state mapping. [Model resources use runtime-created volumes](https://github.com/nosana-ci/nosana-node/blob/997ce206d769230443ba53da52fd539b224880be/packages/job-runtime/src/resources/VolumeCache.ts),
including [Hugging Face downloads into a mounted volume](https://github.com/nosana-ci/nosana-node/blob/997ce206d769230443ba53da52fd539b224880be/packages/job-runtime/src/resources/helpers/createHFArgs.ts).
They do not require a host-level `HF_HOME` setting. Workload/model downloads
after activation are expected and land in that runtime storage.

## Install before the final reboot

The [official wrapper](https://github.com/nosana-ci/nosana-node/blob/997ce206d769230443ba53da52fd539b224880be/auto-update/src/index.ts)
installs its node npm package every time it starts. Merely pulling the official
wrapper image would leave software download until activation.

Preparation therefore builds one local derived image from the pinned wrapper.
It checks the package's registry integrity value, installs version 1.1.91 with
its published npm shrinkwrap, then proves a second installation works in a
separate Docker build step with `RUN --network=none` and `npm --offline`, including
its dependency lifecycle scripts. The image retains the installed package and its npm cache and
sets `NOSANA_NODE_VERSION=1.1.91` and `npm_config_offline=true`. The upstream
wrapper code and entrypoint are unchanged. Its initial activation reinitializes
from cached bytes, without downloading node software. Its pinned-version path
rejects automatic package replacement.

The build runs npm and a JSON version check, **never the node executable**.
No GPU or loaded driver is needed for this preparation. The published package
has no preinstall/install/postinstall command that starts the node. The four
production dependencies marked with install scripts in the reviewed shrinkwrap
are `bufferutil@4.0.7` and `utf-8-validate@5.0.10` (`node-gyp-build`),
`cpu-features@0.0.9` (native build check/node-gyp), and `ssh2@1.15.0` (its native
extension installer). The package's `dev:start` scripts are not installation
lifecycle commands. See [npm lifecycle semantics](https://docs.npmjs.com/cli/v11/using-npm/scripts/)
and [published shrinkwrap semantics](https://docs.npmjs.com/cli/v11/configuring-npm/npm-shrinkwrap-json/).

The derived image ID, build recipe hash and ownership labels are checked on
resume. Interrupted downloads/builds can be retried. Existing state, edited
managed files or different images are refused; no cleanup deletes possible
wallet data. The Dockerfile is sent over stdin, so package cache and image
layers stay in the configured Docker storage rather than a host build folder.

## Explicit activation

`--start-nosana` verifies the completed storage journal, mount guard, effective
Docker/containerd roots and prepared files/images. It requires an interactive
operator to type `START NOSANA` after explaining the network/transaction
boundary. Then it checks the NVIDIA driver and toolkit, starts the installed
NVIDIA persistence service, and checks NVIDIA access using the prepared image.
A driver needing a reboot stops activation with an explanation.

Only then does it create/start the outer container using the classic launch
contract: `--gpus=all`, `/dev/fuse`, privileged mode, persistence-directory bind,
`ENABLE_GPU=true`, `NVIDIA_DRIVER_CAPABILITIES=all`, the state bind and cache
volume. The prepared node image is streamed from Docker into Podman without a
temporary archive on the OS disk. The inner container gets the state bind,
`/podman.sock` at `/root/.nosana/podman/podman.sock`, `NOSANA_GATEWAY`
(`192.168.101.0/24`, gateway `192.168.101.1`) and `start --network mainnet`.
Neither container receives an automatic restart policy or boot activation unit.

Docker 29's containerd image store reports a manifest/index digest as its image
ID; the tested Podman 4.5 import reports the config digest as `Id` and retains
the Docker manifest digest as `Digest`. Activation resolves the unique imported
config ID through that measured mapping and verifies the inspected image's
ownership, build recipe, package version and offline environment. Older Docker
config IDs are matched directly. The node container is created and checked
against the resulting Podman config ID.

Existing managed resources are checked again before reuse: the cache must be
an ordinary local volume under the selected Docker root; inner state/socket
mounts, image, command, network and pinned/offline environment must match.
Foreign resources are preserved. Rerunning preparation does not restart a live
node. An already running managed node is not restarted by activation either.

This boundary matters: [node startup](https://github.com/nosana-ci/nosana-node/blob/997ce206d769230443ba53da52fd539b224880be/src/NodeManager/index.ts)
can request registration/qualification and enter the market. The
[health handler](https://github.com/nosana-ci/nosana-node/blob/997ce206d769230443ba53da52fd539b224880be/src/NodeManager/node/health/healthHandler.ts)
and [stake handler](https://github.com/nosana-ci/nosana-node/blob/997ce206d769230443ba53da52fd539b224880be/src/NodeManager/node/stake/StakeHandler.ts)
can automatically submit token-account and zero-amount staking/rewards-account
transactions before an optional NOS top-up prompt. Installation tests therefore
never activate a node, even as a supposed health check. No wallet seed is
printed, requested, copied into the OS configuration or included in evidence.

## Native agent is a different installation

The current [native host-agent v1.0.8](https://github.com/nosana-ci/host-agent/releases/tag/v1.0.8)
is commit `6689354357309d740c2004560af71b21be159f88`. Its
[fixed layout](https://github.com/nosana-ci/host-agent/blob/6689354357309d740c2004560af71b21be159f88/internal/config/config.go)
uses `/var/lib/nosana` for the agent, bundled Podman, graph root and node state;
it is independent of Docker's data root. Its
[install routine](https://github.com/nosana-ci/host-agent/blob/6689354357309d740c2004560af71b21be159f88/internal/install/install.go)
creates a wallet and enables/starts both services. It has no reviewed
install-only/data-directory switch. This fork refuses an existing native agent
and does not silently install or migrate to it.

## Proof boundary

Unit tests use independent command/filesystem fixtures and execute no host
services or Nosana node. The VM gate checks real pinned image downloads, image
build/offline npm cache and storage/reboot behavior on the supported Ubuntu
versions. A VM without a GPU cannot prove driver loading, GPU qualification,
market admission or successful production jobs; these remain activation-time
checks on the operator's machine. No live financial action is part of testing.
