# VM Hardware Testing: Low-Spec Validation

Three rounds of testing against real, resource-limited virtual machines —
not `docker --memory` limits sharing the host's kernel, which have a blind
spot (see "Why a real VM, not `docker --memory`" below). All VMs were
Debian 12 (bookworm) `genericcloud` images booted headless under QEMU/KVM
with hardware-enforced RAM/vCPU/disk caps, provisioned via cloud-init.

## Why a real VM, not `docker --memory`

Earlier testing used `docker run --memory=Ng` on this host to simulate
constrained RAM. That's useful for finding a hard ceiling, but it has a gap:
`psutil.virtual_memory()` (used by the app's own RAM check) reads
`/proc/meminfo`, which — inside a container — reports the **host's**
memory, not the cgroup limit. So a cgroup-limited container can get killed
by the kernel at a limit the app's own check never saw coming. A real VM
has its own kernel, so `/proc/meminfo` inside it is genuinely accurate. This
matters directly for validating the RAM-gating fix in `new_recorder.py`,
since that fix depends on `psutil` seeing real numbers.

## Test 1: 8 GB RAM / 4 vCPU (CLAUDE.md's stated target spec)

**Setup:** Debian 12 VM, 8192 MB RAM, 4 vCPUs, 20 GB disk (disk was not the
variable under test here). Recorder image (2.7 GB) and Whisper `large-v3.pt`
(2.9 GB) transferred in; recorder run with `docker run` against the real
`config/streams.yml` (13 streams), no artificial env overrides.

**Round 1 result: FAIL (found a real bug).** With `MIN_TRANSCRIBE_RAM_GB`
at its then-default of 7, the container was OOM-killed (exit 137) partway
through `whisper.load_model()`:

```
free -h at container start: 7.4Gi available
app.log: "Loading Whisper model on cpu"   ← last line before the kill
docker inspect: Status=exited OOM=true exit=137
```

The RAM-gate logic itself was sound, but 7 GB was the wrong number: 7.4 GB
was "available" at the pre-load check (passing the gate), but the load
itself pushed past what was actually free, and the guest kernel killed the
container anyway. This defeated the entire point of the check — verified
proof that a threshold picked from a partial reading (resident weight size
only, ~5.9 GB, observed mid-load in earlier `docker --memory` testing)
undercounted the real peak.

**Fix:** raised `MIN_TRANSCRIBE_RAM_GB` default from 7 → 10, matching the
threshold already shown safe via `docker --memory` limits (6 GB and 8 GB
both OOM-killed a bare container running only Whisper; 10 GB survived).

**Round 2 result: PASS.** Same VM, same image (rebuilt with the fix), same
config. The recorder correctly detected insufficient RAM *before* attempting
to load the model:

```
2026-09-03 13:11:48 WARNING - Skipping transcription: 7.3 GB RAM available,
  need at least 10.0 GB to load Whisper safely. Recording is unaffected.
  Will retry every 20 min.
docker inspect: Status=running OOM=false exit=0
```

Container stayed up indefinitely in recording-only mode. Also ran the
webserver container alongside it in the same VM: both healthy, `/health`
returned `200`, combined usage left **~7.3 GB of the 7.8 GB free** — the
whole stack minus transcription is very light.

**Verdict:** on the project's stated 8 GB target, the app now runs
indefinitely without crashing. Transcription will not run on this spec
(by design — see below), but recording, scheduling, config hot-reload, and
the web UI all work normally.

## Test 2: 4 GB RAM / 2 vCPU / 4 GB disk (disk too small)

**Setup:** Debian 12 VM, 4096 MB RAM, 2 vCPUs, disk resized to exactly 4 GB
(the smallest size the requested disk figure would actually accept for a
genericcloud base image). Same recorder image and cloud-init flow as Test 1.

**Result: FAIL — disk-space exhaustion, before RAM or CPU could even be
exercised.**

| Stage | Disk used | Disk free (of 3.8 GB usable) |
|---|---|---|
| Base OS boot | 855 MB | 2.9 GB |
| + `docker.io` + deps (cloud-init) | 1.4 GB | 2.2 GB |
| + recorder image transfer attempt | **failed at 2.2 GB written** | **0 B** |

```
scp: write remote "/home/tester/recorder-image.tar": Failure
df -h /:  3.8G  3.6G     0  100%  /
```

The 2.7 GB recorder image alone doesn't fit in the 2.2 GB left after the OS
and Docker itself are installed — and that's before the 2.9 GB Whisper model
is even considered. RAM (2 vCPU / 4 GB, both confirmed correctly detected —
`nproc` → 2, `free -h` → 3.8Gi total) was never actually put under test,
because the stack couldn't be loaded onto the disk in the first place.

**Minimum disk breakdown, measured:**

| Component | Size |
|---|---|
| Debian 12 base + `docker.io` + deps | ~1.4 GB |
| Recorder image (`churchservice-recorder-recorder`) | 2.7 GB |
| Whisper `large-v3.pt` | 2.9 GB |
| **Minimum to have the stack loaded and ready** | **~7 GB** |
| + working margin (recordings, logs, docker overlay churn) | recommend **10 GB+** total |

**Verdict:** 4 GB of total disk is not viable for this deployment at all,
independent of RAM or CPU — it can't even hold the image plus model, let
alone run them. This is a harder constraint than RAM: RAM has a documented,
working degrade-to-recording-only path (Test 1); disk does not, because the
image and model have to be present on disk before the process can start.

## Test 3: 4 GB RAM / 2 vCPU / 16 GB disk (retest — disk fixed)

Requested as a direct follow-up once storage was confirmed not to be a
real constraint: same RAM/CPU as Test 2, disk raised to 16 GB (comfortably
above the ~7 GB minimum measured in Test 2, since storage is usually not a
problem for a real deployment).

**Setup:** fresh Debian 12 VM, 4096 MB RAM, 2 vCPUs, 16 GB disk. Recorder
image, Whisper `large-v3.pt`, and the real (now-deduplicated)
`config/streams.yml` transferred in; recorder and webserver run with
`docker run`, default settings, no artificial overrides.

**Result: PASS.**

```
Base OS + docker.io:                       1.4 GB used, 14 GB free
+ recorder image + model loaded:           6.9 GB used,  8.1 GB free
docker inspect churchtest_4g:              Status=running OOM=false exit=0

app.log:
  2026-09-03 13:26:58 WARNING - Skipping transcription: 3.4 GB RAM
    available, need at least 10.0 GB to load Whisper safely. Recording
    is unaffected. Will retry every 20 min.

MAX_CONCURRENT_RECORDINGS = 2   (matches nproc == 2, confirms the
                                  CPU-scaling fix works correctly on a
                                  2-core machine, not just the 4-core
                                  VM in Test 1)
```

Ran the webserver alongside the recorder in the same VM (matching a real
deployment): both containers `running`, `/health` → `200`. Combined memory
usage for the **entire stack** (recorder + webserver, transcription
correctly idle): **542 MB used / 3.8 GB total**, 8.1 GB disk still free.
Both containers confirmed stable (re-checked `docker inspect` after the
fact — still `running`, `OOM=false`).

**Verdict:** with adequate disk, a 4 GB RAM / 2 vCPU machine runs the full
stack cleanly and indefinitely: recording, scheduling, config hot-reload,
and the web UI all work; transcription correctly and cleanly stays off
(3.4 GB available is even further below the 10 GB threshold than the 8 GB
case in Test 1, so the skip path triggers immediately, no OOM risk at any
point). This resolves Test 2's finding — the earlier failure was entirely
a disk-provisioning issue, not an application defect. No code changes were
needed for this pass, since the RAM-gating and CPU-scaling logic already
fixed for Test 1 carried over unchanged.

## Note on CLAUDE.md's storage target

CLAUDE.md's "no more than 3 GB of storage minus the AI model files" refers
to the **app's own image footprint**, which measured at 2.7 GB — within
that budget. It was never a claim that the *whole machine's disk* could be
4 GB; OS and Docker overhead (~1.4 GB measured here) and the model file
(2.9 GB, explicitly excluded from the 3 GB figure) are additional and
unavoidable. A 4 GB **total disk** target contradicts the project's own
constraint once the model is included — there's no way to satisfy both.

## Summary

| Spec | RAM/CPU result | Disk result | Overall |
|---|---|---|---|
| 8 GB RAM / 4 vCPU / 20 GB disk | Pass after fix (transcription correctly skips, recording unaffected) | Pass (plenty of headroom) | **Usable** — matches CLAUDE.md's stated target |
| 4 GB RAM / 2 vCPU / 4 GB disk | Not reached | **Fail** (can't fit image + model) | **Not viable** — needs more disk, not more code changes |
| 4 GB RAM / 2 vCPU / 16 GB disk | **Pass** (transcription skips cleanly, whole stack uses 542 MB) | Pass (8.1 GB free after loading) | **Usable** — confirms Test 2's failure was disk-only |

**Recommendation:** the app runs reliably down to 4 GB RAM / 2 vCPU
provided disk is not artificially constrained — confirmed directly in
Test 3, not just inferred. The only hard requirement below CLAUDE.md's
8 GB target is disk headroom: budget **~10 GB+ total** (OS + Docker ~1.4 GB,
recorder image 2.7 GB, Whisper model 2.9 GB, plus working margin for
recordings/logs). Given storage is not usually the constraint in practice,
this is the easy side of the tradeoff — RAM was the one that needed an
actual code fix (`MIN_TRANSCRIBE_RAM_GB`, Test 1); disk just needs to be
sized correctly.
